/* Copyright (c) 2026 Amir Fish. All rights reserved. SPDX-License-Identifier: LicenseRef-CCC-Software-License */
(function () {
  'use strict';

  const API = '/api/codex/client';
  const POLL_BASE_MS = 900;
  const POLL_MAX_MS = 12000;
  const MAX_COMPOSER_IMAGE_BYTES = 700 * 1024;
  const COMMON_METHODS = [
    'thread/read', 'thread/list', 'turn/start', 'turn/steer', 'turn/interrupt',
    'thread/fork', 'thread/name/set', 'thread/archive', 'thread/unarchive',
    'thread/goal/get', 'thread/goal/set', 'thread/queue/list', 'thread/queue/add',
    'account/read', 'account/rateLimits/read', 'model/list', 'skills/list',
    'mcpServer/list', 'plugin/list', 'fs/listDirectory', 'fs/readFile',
    'review/start', 'thread/backgroundTerminals/list',
  ];
  const LEGACY_APPROVALS = new Set([
    'execCommandApproval', 'applyPatchApproval',
    'item/execCommand/requestApproval', 'item/applyPatch/requestApproval',
  ]);
  const state = {
    root: null, context: null, catalog: null, schemas: new Map(),
    thread: null, requests: [], generation: null, eventCursor: null,
    historyCursor: null, connected: false, activeSurface: 'conversation',
    activeGroup: '', query: '', toolsOpen: false, pollTimer: null, pollAbort: null,
    pollInFlight: false, pollFailures: 0, closed: true, requestToken: 0,
    mutationLocks: new Map(), responseLocks: new Map(), generationPromise: null, activity: [],
    composerModels: [], composerAttachment: null, composerOptionsSync: null,
    activeRead: null, readRefreshTimer: null, readRefreshInFlight: false, readRefreshQueued: false,
    renderScheduled: false, visibilityHandler: null, composerSync: null, previousDisplay: new Map(),
    mediaController: null, mediaCleanup: Promise.resolve(), mediaCleanupBlocked: false,
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function pretty(value) {
    return String(value || '')
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .replace(/[._/-]+/g, ' ')
      .replace(/\b\w/g, ch => ch.toUpperCase())
      .trim();
  }

  function conciseError(error) {
    if (!error) return 'Something went wrong.';
    if (typeof error === 'string') return error;
    return error.message || error.detail || error.error || 'Something went wrong.';
  }

  function safeUrl(raw, allowImage) {
    const value = String(raw || '').trim();
    if (!value) return '';
    if (allowImage && /^(data:image\/(?:png|jpeg|gif|webp|svg\+xml);base64,|blob:)/i.test(value)) return value;
    try {
      const parsed = new URL(value, window.location.href);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
    } catch (_) {}
    return '';
  }

  function sanitizeHtml(html) {
    const template = document.createElement('template');
    template.innerHTML = String(html || '');
    template.content.querySelectorAll('script,style,iframe,object,embed,form,meta,link,svg,math,canvas').forEach(node => node.remove());
    template.content.querySelectorAll('*').forEach(node => {
      for (const attr of Array.from(node.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on') || ['srcdoc', 'style', 'formaction', 'action', 'xlink:href'].includes(name)) node.removeAttribute(attr.name);
      }
      if (node.hasAttribute('href')) {
        const url = safeUrl(node.getAttribute('href'), false);
        if (url) {
          node.setAttribute('href', url);
          node.setAttribute('target', '_blank');
          node.setAttribute('rel', 'noopener noreferrer');
        } else node.removeAttribute('href');
      }
      if (node.hasAttribute('src')) {
        const url = safeUrl(node.getAttribute('src'), node.tagName === 'IMG');
        if (url) node.setAttribute('src', url);
        else node.remove();
      }
    });
    return template.content;
  }

  function fallbackMarkdown(text) {
    const source = String(text || '');
    const holder = el('div');
    const lines = source.split('\n');
    let paragraph = [];
    let code = null;
    let list = null;
    const inline = value => {
      const span = el('span');
      span.textContent = value;
      let html = span.innerHTML;
      html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
      html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, href) {
        const safe = safeUrl(href, false);
        return safe ? '<a href="' + safe.replaceAll('&', '&amp;').replaceAll('"', '&quot;') + '" target="_blank" rel="noopener noreferrer">' + label + '</a>' : label;
      });
      return html;
    };
    const flush = () => {
      if (paragraph.length) {
        const p = el('p');
        p.innerHTML = inline(paragraph.join(' '));
        holder.append(p);
        paragraph = [];
      }
      list = null;
    };
    for (const line of lines) {
      if (/^```/.test(line)) {
        if (code !== null) {
          const pre = el('pre'); const c = el('code', '', code.join('\n')); pre.append(c); holder.append(pre); code = null;
        } else { flush(); code = []; }
        continue;
      }
      if (code !== null) { code.push(line); continue; }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) { flush(); const h = el('h' + Math.min(4, heading[1].length + 1)); h.innerHTML = inline(heading[2]); holder.append(h); continue; }
      const bullet = line.match(/^\s*[-*]\s+(.+)$/);
      if (bullet) {
        flush();
        if (!list) { list = el('ul'); holder.append(list); }
        const li = el('li'); li.innerHTML = inline(bullet[1]); list.append(li); continue;
      }
      if (!line.trim()) { flush(); continue; }
      paragraph.push(line.trim());
    }
    if (code !== null) { const pre = el('pre'); pre.append(el('code', '', code.join('\n'))); holder.append(pre); }
    flush();
    return holder.innerHTML;
  }

  function markdownNode(text) {
    const node = el('div', 'codex-client-markdown');
    let rendered = '';
    try {
      rendered = typeof window.CCCCodexMarkdown === 'function'
        ? window.CCCCodexMarkdown(String(text || ''))
        : fallbackMarkdown(text);
    } catch (_) { rendered = fallbackMarkdown(text); }
    node.append(sanitizeHtml(rendered));
    return node;
  }

  function itemKind(item) {
    return String(item && (item.type || item.kind || item.itemType || item.item_type) || 'unknown');
  }

  function textFrom(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(textFrom).filter(Boolean).join('\n');
    if (!value || typeof value !== 'object') return '';
    if (typeof value.text === 'string') return value.text;
    if (typeof value.content === 'string') return value.content;
    if (Array.isArray(value.content)) return textFrom(value.content);
    if (typeof value.summary === 'string') return value.summary;
    if (typeof value.message === 'string') return value.message;
    return '';
  }

  function sensitiveKey(key) { return /(?:password|passphrase|secret|token|api.?key|private.?key|credential)/i.test(String(key || '')); }

  function valueSummary(value) {
    if (value === null) return 'None';
    if (value === undefined) return '';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'string' || typeof value === 'number') return String(value);
    if (Array.isArray(value)) return value.map(valueSummary).filter(Boolean).join(', ');
    if (typeof value === 'object') {
      return Object.entries(value).slice(0, 6).map(([key, child]) => pretty(key) + ': ' + (sensitiveKey(key) ? 'Hidden' : valueSummary(child))).join(' · ');
    }
    return String(value);
  }

  function keyValueView(value, omitted) {
    const dl = el('dl', 'codex-client-facts');
    if (!value || typeof value !== 'object') return dl;
    const skip = new Set(omitted || []);
    Object.entries(value).forEach(([key, child]) => {
      if (skip.has(key) || child === undefined || child === null || child === '') return;
      const dt = el('dt', '', pretty(key));
      const dd = el('dd', '', sensitiveKey(key) ? 'Hidden' : valueSummary(child));
      dl.append(dt, dd);
    });
    return dl;
  }

  function statusPill(status) {
    const value = String(status || '').toLowerCase();
    return el('span', 'codex-client-status ' + (value ? 'is-' + value.replace(/[^a-z0-9]+/g, '-') : ''), pretty(value || 'Active'));
  }

  function detailsCard(kind, item, title, body, open) {
    const card = el('details', 'codex-client-item codex-client-activity is-' + kind.replace(/[^a-z0-9]+/gi, '-').toLowerCase());
    card.dataset.itemKey = String(item.id || item.itemId || item.callId || kind + '-' + (item.ts || ''));
    card.open = !!open;
    const summary = el('summary');
    summary.append(el('span', 'codex-client-activity-icon', activityIcon(kind)), el('span', 'codex-client-activity-title', title));
    if (item.status) summary.append(statusPill(item.status));
    card.append(summary, body);
    return card;
  }

  function diffNode(diff) {
    const pre = el('pre', 'codex-client-diff');
    String(diff || '').split('\n').forEach((line, index, lines) => {
      const span = el('span', line.startsWith('@@') ? 'codex-diff-hunk'
        : line.startsWith('+') && !line.startsWith('+++') ? 'codex-diff-add'
        : line.startsWith('-') && !line.startsWith('---') ? 'codex-diff-delete' : '', line);
      pre.append(span);
      if (index < lines.length - 1) pre.append(document.createTextNode('\n'));
    });
    return pre;
  }

  function appendFileChanges(body, changes) {
    const entries = Array.isArray(changes)
      ? changes.map((change, index) => [change && (change.path || change.filePath || change.file_path) || 'Change ' + (index + 1), change])
      : changes && typeof changes === 'object' ? Object.entries(changes) : [];
    entries.forEach(([filePath, change]) => {
      const row = el('div', 'codex-client-file-change');
      const base = state.context && state.context.repoPath;
      const displayPath = base && String(filePath).startsWith(base.replace(/\/$/, '') + '/')
        ? String(filePath).slice(base.replace(/\/$/, '').length + 1) : filePath;
      const head = el('div', 'codex-client-file-change-head'); head.append(el('strong', '', displayPath));
      const rawKind = change && (change.kind || change.type || change.status);
      const kind = rawKind && typeof rawKind === 'object' ? rawKind.type : rawKind;
      if (kind) head.append(el('span', 'codex-client-badge', pretty(kind)));
      row.append(head);
      const diff = change && (change.diff || change.unified_diff || change.patch);
      if (diff) row.append(diffNode(diff));
      if (change && typeof change === 'object') row.append(keyValueView(change, ['path', 'filePath', 'file_path', 'kind', 'type', 'status', 'diff', 'unified_diff', 'patch']));
      body.append(row);
    });
  }

  function activityIcon(kind) {
    if (/command|terminal|shell/i.test(kind)) return '›_';
    if (/file|patch|change/i.test(kind)) return '±';
    if (/web|search/i.test(kind)) return '⌕';
    if (/image|media/i.test(kind)) return '◫';
    if (/collab|task/i.test(kind)) return '⌘';
    if (/review/i.test(kind)) return '✓';
    if (/reason/i.test(kind)) return '◇';
    return '•';
  }

  function renderItem(item) {
    item = item && typeof item === 'object' ? item : { type: 'unknown', value: item };
    const kind = itemKind(item);
    const normalized = kind.toLowerCase();
    if (normalized === 'usermessage' || normalized === 'user_message') {
      const row = el('article', 'codex-client-item codex-client-message is-user');
      row.append(markdownNode(textFrom(item)));
      return row;
    }
    if (normalized === 'agentmessage' || normalized === 'agent_message' || normalized === 'assistantmessage') {
      const phase = String(item.phase || '').toLowerCase();
      const final = phase === 'final_answer' || phase === 'final' || item.final === true;
      const row = el('article', 'codex-client-item codex-client-message is-agent ' + (final ? 'is-final-answer' : 'is-commentary'));
      const label = el('div', 'codex-client-message-phase', final ? 'Answer' : 'Working');
      row.append(label, markdownNode(textFrom(item)));
      return row;
    }
    if (normalized === 'plan' || item.plan) {
      const body = el('div', 'codex-client-card-body');
      const steps = item.steps || item.plan && (item.plan.steps || item.plan) || [];
      if (textFrom(item)) body.append(markdownNode(textFrom(item)));
      if (Array.isArray(steps)) {
        const list = el('ol', 'codex-client-plan');
        steps.forEach(step => {
          const li = el('li', step && step.completed ? 'is-complete' : '');
          li.append(el('span', 'codex-client-plan-mark', step && step.completed ? '✓' : '○'), el('span', '', textFrom(step) || valueSummary(step)));
          list.append(li);
        });
        body.append(list);
      }
      return detailsCard('plan', item, item.title || 'Plan', body, true);
    }
    if (/reasoning/.test(normalized)) {
      const body = el('div', 'codex-client-card-body'); body.append(markdownNode(textFrom(item) || item.summary || 'Reasoning update'));
      return detailsCard('reasoning', item, 'Reasoning', body, false);
    }
    if (/commandexecution|command_execution|shellcommand|terminal/.test(normalized)) {
      const body = el('div', 'codex-client-card-body');
      const command = item.command || item.cmd || item.input;
      if (command) { const pre = el('pre', 'codex-client-command'); pre.append(el('code', '', Array.isArray(command) ? command.join(' ') : valueSummary(command))); body.append(pre); }
      const output = textFrom(item.output || item.aggregatedOutput || item.result);
      if (output) { const pre = el('pre', 'codex-client-output', output); body.append(pre); }
      body.append(keyValueView(item, ['type', 'kind', 'command', 'cmd', 'input', 'output', 'aggregatedOutput', 'result']));
      const card = detailsCard('command', item, item.title || 'Command', body, item.status === 'inProgress');
      card.dataset.terminalItem = item.id || '';
      return card;
    }
    if (/filechange|file_change|patch|diff/.test(normalized)) {
      const body = el('div', 'codex-client-card-body');
      const changes = item.changes || item.files;
      appendFileChanges(body, changes);
      const diff = item.diff || item.patch;
      if (diff) body.append(diffNode(diff));
      return detailsCard('file-change', item, item.title || 'File changes', body, true);
    }
    if (/imageview|imagegeneration|image|media/.test(normalized)) {
      const body = el('div', 'codex-client-card-body codex-client-media');
      const url = safeUrl(item.url || item.imageUrl || item.image_url || item.path, true);
      if (url) { const image = el('img'); image.src = url; image.alt = item.alt || item.title || 'Generated image'; image.loading = 'lazy'; body.append(image); }
      if (textFrom(item)) body.append(markdownNode(textFrom(item)));
      const card = detailsCard('media', item, item.title || (/generation/.test(normalized) ? 'Image generation' : 'Image'), body, true);
      card.dataset.mediaItem = item.id || '';
      return card;
    }
    if (/review/.test(normalized) || /compaction/.test(normalized)) {
      const body = el('div', 'codex-client-card-body'); body.append(markdownNode(textFrom(item) || valueSummary(item)));
      return detailsCard(/review/.test(normalized) ? 'review' : 'compaction', item, item.title || pretty(kind), body, true);
    }
    if (/toolcall|tool_call|websearch|dynamictool|mcptool|collabtool/.test(normalized)) {
      const body = el('div', 'codex-client-card-body');
      const text = textFrom(item);
      if (text) body.append(markdownNode(text));
      body.append(keyValueView(item, ['type', 'kind', 'text', 'content', 'message', 'status', 'id', 'itemId']));
      const title = item.title || item.tool || item.name || (/websearch/.test(normalized) ? 'Web search' : 'Tool activity');
      return detailsCard(kind, item, title, body, item.status === 'inProgress');
    }
    const body = el('div', 'codex-client-card-body');
    const text = textFrom(item);
    if (text) body.append(markdownNode(text));
    body.append(keyValueView(item, ['type', 'kind', 'text', 'content', 'message']));
    return detailsCard('unknown', item, item.title || pretty(kind) || 'Update', body, false);
  }

  function decodePointerPart(part) { return part.replace(/~1/g, '/').replace(/~0/g, '~'); }

  function resolveRef(schema, root, seen) {
    if (!schema || typeof schema !== 'object' || !schema.$ref || !schema.$ref.startsWith('#/')) return schema || {};
    const refs = seen || new Set();
    if (refs.has(schema.$ref)) return {};
    refs.add(schema.$ref);
    let found = root;
    for (const part of schema.$ref.slice(2).split('/').map(decodePointerPart)) found = found && found[part];
    if (!found || typeof found !== 'object') return {};
    return Object.assign({}, resolveRef(found, root, refs), Object.fromEntries(Object.entries(schema).filter(([key]) => key !== '$ref')));
  }

  function mergeAllOf(schema, root) {
    schema = resolveRef(schema, root);
    if (!Array.isArray(schema.allOf)) return schema;
    const merged = Object.assign({}, schema);
    delete merged.allOf;
    merged.properties = Object.assign({}, merged.properties || {});
    merged.required = Array.isArray(merged.required) ? merged.required.slice() : [];
    schema.allOf.forEach(branch => {
      branch = mergeAllOf(branch, root);
      Object.assign(merged.properties, branch.properties || {});
      for (const req of branch.required || []) if (!merged.required.includes(req)) merged.required.push(req);
      for (const [key, value] of Object.entries(branch)) if (!['properties', 'required'].includes(key) && merged[key] === undefined) merged[key] = value;
    });
    return merged;
  }

  function allowsNull(schema) {
    if (!schema || typeof schema !== 'object') return false;
    if (schema.type === 'null' || Array.isArray(schema.type) && schema.type.includes('null')) return true;
    return ['oneOf', 'anyOf'].some(key => Array.isArray(schema[key]) && schema[key].some(branch => branch && (branch.type === 'null' || branch.const === null)));
  }

  function withoutNull(schema) {
    const copy = Object.assign({}, schema);
    if (Array.isArray(copy.type)) copy.type = copy.type.filter(type => type !== 'null');
    for (const key of ['oneOf', 'anyOf']) if (Array.isArray(copy[key])) copy[key] = copy[key].filter(branch => !(branch && (branch.type === 'null' || branch.const === null)));
    return copy;
  }

  function typeOfSchema(schema) {
    if (Array.isArray(schema.type)) return schema.type.find(type => type !== 'null') || 'string';
    if (schema.type) return schema.type;
    if (schema.properties || schema.additionalProperties) return 'object';
    if (schema.items) return 'array';
    if (schema.const !== undefined) return typeof schema.const;
    return 'string';
  }

  function variantLabel(schema, index) {
    if (schema.title) return schema.title;
    const props = schema.properties || {};
    for (const [key, value] of Object.entries(props)) if (value && value.const !== undefined) return pretty(value.const || key);
    return 'Option ' + (index + 1);
  }

  function productDescription(descriptor) {
    const description = String(descriptor && descriptor.description || '').trim();
    if (!description) return '';
    if (/\b(?:rpc|wire format|app[- ]server|internal|implementation|serde|json schema)\b|\b[a-z]+_[a-z0-9_]+\b/i.test(description)) return '';
    return description;
  }

  function chooseVariant(branches, value, root) {
    if (!branches.length) return 0;
    if (value && typeof value === 'object') {
      const match = branches.findIndex(branch => {
        branch = mergeAllOf(branch, root);
        return Object.entries(branch.properties || {}).every(([key, prop]) => prop.const === undefined || value[key] === prop.const);
      });
      if (match >= 0) return match;
    }
    return 0;
  }

  function fieldLabel(schema, name, required) {
    const label = el('span', 'codex-schema-label-text', schema.title || pretty(name || 'Value'));
    if (required) label.append(el('span', 'codex-schema-required', ' Required'));
    return label;
  }

  function createForm(schema, initial, context) {
    const rootSchema = schema && typeof schema === 'object' ? schema : { type: 'object' };
    const validators = [];
    const contextValue = (name, value) => {
      if (value !== undefined && value !== null && value !== '') return value;
      if (/^thread_?id$/i.test(name)) return context && context.threadId || value;
      if (/^(cwd|repo_?path|working_?directory)$/i.test(name)) return context && context.repoPath || value;
      if (/^environment_?id$/i.test(name)) return context && context.environmentId || value;
      return value;
    };

    function buildAny(value, name, required, path) {
      const wrap = el('div', 'codex-schema-field codex-schema-any');
      wrap.dataset.schemaPath = path || name || '';
      const label = el('label', 'codex-schema-label'); label.append(fieldLabel({}, name, required));
      const select = el('select', 'codex-schema-input'); select.dataset.anyValueType = 'true';
      [['object', 'Key/value group'], ['array', 'List'], ['string', 'Text'], ['number', 'Number'], ['boolean', 'Yes or no'], ['null', 'No value']]
        .forEach(([type, title]) => { const option = el('option', '', title); option.value = type; select.append(option); });
      const initialType = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value === 'object' && value ? 'object'
        : typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string';
      select.value = initialType;
      const slot = el('div', 'codex-schema-any-slot');
      const schemaFor = type => type === 'object' ? { type: 'object', additionalProperties: true }
        : type === 'array' ? { type: 'array', items: true }
        : type === 'null' ? { const: null }
        : { type };
      let child = build(schemaFor(initialType), value, '', false, path);
      slot.append(child.element);
      select.addEventListener('change', () => {
        child = build(schemaFor(select.value), undefined, '', false, path);
        slot.replaceChildren(child.element);
      });
      label.append(select, slot); wrap.append(label);
      if (required) wrap.setAttribute('aria-required', 'true');
      return { element: wrap, read: () => child.read(), empty: () => false };
    }

    function build(rawSchema, value, name, required, path) {
      if (rawSchema === true) return buildAny(value, name, required, path);
      let current = mergeAllOf(rawSchema || {}, rootSchema);
      const nullable = allowsNull(current);
      if (nullable) current = withoutNull(current);
      value = contextValue(name, value);
      const wrap = el('div', 'codex-schema-field');
      wrap.dataset.schemaPath = path || name || '';
      let nullToggle = null;
      if (nullable) {
        const nullRow = el('label', 'codex-schema-null');
        nullToggle = el('input'); nullToggle.type = 'checkbox'; nullToggle.dataset.nullToggle = 'true'; nullToggle.checked = value === null;
        nullRow.append(nullToggle, el('span', '', 'No value'));
        wrap.append(nullRow);
      }
      let branches = current.oneOf || current.anyOf;
      if (Array.isArray(branches) && branches.length === 1) {
        const branch = mergeAllOf(branches[0], rootSchema);
        const outer = Object.assign({}, current);
        if (outer.oneOf === branches) delete outer.oneOf;
        if (outer.anyOf === branches) delete outer.anyOf;
        current = Object.assign(outer, branch);
        branches = current.oneOf || current.anyOf;
      }
      if (Array.isArray(branches) && branches.length > 1) {
        const label = el('label', 'codex-schema-label'); label.append(fieldLabel(current, name, required));
        const select = el('select', 'codex-schema-input'); select.dataset.schemaVariant = 'true';
        branches.forEach((branch, index) => { const option = el('option', '', variantLabel(mergeAllOf(branch, rootSchema), index)); option.value = String(index); select.append(option); });
        let selected = chooseVariant(branches, value, rootSchema); select.value = String(selected);
        const slot = el('div', 'codex-schema-variant-slot');
        let child = build(branches[selected], value, name, required, path);
        slot.append(child.element); label.append(select); wrap.append(label, slot);
        select.addEventListener('change', () => {
          selected = Number(select.value) || 0;
          child = build(branches[selected], undefined, name, required, path);
          slot.replaceChildren(child.element);
        });
        return { element: wrap, read: async () => nullToggle && nullToggle.checked ? null : child.read(), empty: () => false };
      }
      if (current.const !== undefined) {
        const hidden = el('input'); hidden.type = 'hidden'; hidden.name = name || ''; hidden.value = String(current.const); wrap.append(hidden);
        return { element: wrap, read: async () => current.const, empty: () => false };
      }
      const type = typeOfSchema(current);
      if (type === 'object') {
        const fieldset = el('fieldset', 'codex-schema-object');
        if (name || current.title) fieldset.append(el('legend', '', current.title || pretty(name)));
        if (current.description) fieldset.append(el('p', 'codex-schema-help', current.description));
        const requiredFields = new Set(current.required || []);
        const children = [];
        Object.entries(current.properties || {}).forEach(([key, childSchema]) => {
          const childValue = value && typeof value === 'object' ? value[key] : undefined;
          const child = build(childSchema, childValue, key, requiredFields.has(key), (path ? path + '.' : '') + key);
          children.push({ key, child, required: requiredFields.has(key), supplied: childValue !== undefined });
          fieldset.append(child.element);
        });
        let mapRows = [];
        const namedKeys = new Set(Object.keys(current.properties || {}));
        const additionalSchema = current.additionalProperties;
        if (additionalSchema === true || additionalSchema && typeof additionalSchema === 'object') {
          const map = el('div', 'codex-schema-map');
          const rows = el('div', 'codex-schema-map-rows');
          const add = el('button', 'codex-schema-add', 'Add entry'); add.type = 'button'; add.dataset.mapAdd = 'true';
          const addRow = (key, mapValue) => {
            const row = el('div', 'codex-schema-map-row');
            const keyInput = el('input', 'codex-schema-input'); keyInput.placeholder = 'Key'; keyInput.value = key || '';
            const child = build(additionalSchema, mapValue, '', false, (path || name || 'map') + '.*');
            const remove = el('button', 'codex-schema-remove', 'Remove'); remove.type = 'button'; remove.addEventListener('click', () => { row.remove(); mapRows = mapRows.filter(entry => entry.row !== row); });
            row.append(keyInput, child.element, remove); rows.append(row); mapRows.push({ row, keyInput, child });
          };
          if (value && typeof value === 'object') Object.entries(value).forEach(([key, mapValue]) => {
            if (!namedKeys.has(key)) addRow(key, mapValue);
          });
          add.addEventListener('click', () => addRow('', undefined));
          map.append(rows, add); fieldset.append(map);
        }
        if (required) wrap.setAttribute('aria-required', 'true');
        const minimumProperties = Number(current.minProperties || 0);
        if (minimumProperties > 0) validators.push(() => {
          if (nullToggle && nullToggle.checked) { wrap.removeAttribute('aria-invalid'); return ''; }
          const namedCount = children.filter(entry => !entry.child.empty()).length;
          const mapCount = mapRows.filter(entry => entry.keyInput.value.trim()).length;
          const invalid = namedCount + mapCount < minimumProperties;
          if (invalid) wrap.setAttribute('aria-invalid', 'true'); else wrap.removeAttribute('aria-invalid');
          return invalid ? (current.title || pretty(name || 'This group')) + ' needs at least ' + minimumProperties + ' value' + (minimumProperties === 1 ? '.' : 's.') : '';
        });
        wrap.append(fieldset);
        return {
          element: wrap,
          read: async () => {
            if (nullToggle && nullToggle.checked) return null;
            const out = {};
            for (const entry of children) {
              const childValue = await entry.child.read();
              if (entry.required || entry.supplied || !entry.child.empty() || childValue === null) out[entry.key] = childValue;
            }
            for (const entry of mapRows) if (entry.keyInput.value.trim()) out[entry.keyInput.value.trim()] = await entry.child.read();
            return out;
          },
          empty: () => children.every(entry => !entry.required && entry.child.empty()) && mapRows.length === 0,
        };
      }
      if (type === 'array') {
        const label = el('div', 'codex-schema-label'); label.append(fieldLabel(current, name, required));
        if (current.description) label.append(el('span', 'codex-schema-help', current.description));
        const rows = el('div', 'codex-schema-array');
        let children = [];
        const add = el('button', 'codex-schema-add', 'Add ' + (current.items && current.items.title ? current.items.title.toLowerCase() : 'item')); add.type = 'button'; add.dataset.arrayAdd = 'true';
        const addRow = arrayValue => {
          const row = el('div', 'codex-schema-array-row');
          const child = build(current.items || {}, arrayValue, '', false, (path || name || 'items') + '[]');
          const remove = el('button', 'codex-schema-remove', 'Remove'); remove.type = 'button'; remove.addEventListener('click', () => { row.remove(); children = children.filter(entry => entry.row !== row); });
          row.append(child.element, remove); rows.append(row); children.push({ row, child });
        };
        (Array.isArray(value) ? value : []).forEach(addRow);
        add.addEventListener('click', () => addRow(undefined));
        label.append(rows, add); wrap.append(label);
        if (required) wrap.setAttribute('aria-required', 'true');
        const minimumItems = Number(current.minItems || 0);
        if (minimumItems > 0) validators.push(() => {
          if (nullToggle && nullToggle.checked) { wrap.removeAttribute('aria-invalid'); return ''; }
          const invalid = children.length < minimumItems;
          if (invalid) wrap.setAttribute('aria-invalid', 'true'); else wrap.removeAttribute('aria-invalid');
          return invalid ? (current.title || pretty(name || 'This list')) + ' needs at least ' + minimumItems + ' item' + (minimumItems === 1 ? '.' : 's.') : '';
        });
        return { element: wrap, read: async () => nullToggle && nullToggle.checked ? null : Promise.all(children.map(entry => entry.child.read())), empty: () => children.length === 0 };
      }
      const label = el('label', 'codex-schema-label'); label.append(fieldLabel(current, name, required));
      if (current.description) label.append(el('span', 'codex-schema-help', current.description));
      let input;
      const enumValues = current.enum;
      if (Array.isArray(enumValues)) {
        input = el('select', 'codex-schema-input');
        if (!required) { const blank = el('option', '', 'Choose…'); blank.value = ''; input.append(blank); }
        enumValues.forEach(optionValue => { const option = el('option', '', pretty(optionValue)); option.value = String(optionValue); input.append(option); });
        if (value !== undefined && value !== null) input.value = String(value);
      } else if (type === 'boolean') {
        input = el('input'); input.type = 'checkbox'; input.checked = value === true;
      } else if (current.contentEncoding === 'base64' || /base64/i.test(current.format || '')) {
        const adapter = el('div', 'codex-schema-file-adapter'); adapter.dataset.fileAdapter = 'true';
        input = el('textarea', 'codex-schema-input codex-schema-file-text'); input.value = value || ''; input.placeholder = 'Paste text, or choose a file';
        const picker = el('input'); picker.type = 'file';
        picker.addEventListener('change', () => {
          const file = picker.files && picker.files[0]; if (!file) return;
          const reader = new FileReader(); reader.onload = () => { input.value = String(reader.result || '').split(',').pop() || ''; }; reader.readAsDataURL(file);
        });
        adapter.append(picker, input); label.append(adapter); wrap.append(label);
        return { element: wrap, read: async () => nullToggle && nullToggle.checked ? null : input.value, empty: () => !input.value };
      } else if (type === 'number' || type === 'integer') {
        input = el('input', 'codex-schema-input'); input.type = 'number'; if (type === 'integer') input.step = '1'; if (current.minimum !== undefined) input.min = current.minimum; if (current.maximum !== undefined) input.max = current.maximum; if (value !== undefined && value !== null) input.value = String(value);
      } else {
        input = (current.format === 'multiline' || current.format === 'markdown' || current.maxLength > 240) ? el('textarea', 'codex-schema-input') : el('input', 'codex-schema-input');
        if (input.tagName === 'INPUT') input.type = current.writeOnly || current.format === 'password' || /secret|token|password|api.?key/i.test(name || '') ? 'password' : 'text';
        if (value !== undefined && value !== null) input.value = String(value);
      }
      input.name = name || '';
      if (required) {
        input.setAttribute('aria-required', 'true');
        if (type !== 'boolean' && !nullable) input.required = true;
      }
      if (current.minLength !== undefined && 'minLength' in input) input.minLength = Number(current.minLength);
      if (current.maxLength !== undefined && 'maxLength' in input) input.maxLength = Number(current.maxLength);
      if (current.pattern && input.tagName === 'INPUT') input.pattern = current.pattern;
      if (current.default !== undefined && (value === undefined || value === null)) {
        if (type === 'boolean') input.checked = current.default === true; else input.value = String(current.default);
      }
      label.append(input); wrap.append(label);
      return {
        element: wrap,
        read: async () => {
          if (nullToggle && nullToggle.checked) return null;
          if (type === 'boolean') return input.checked;
          if ((type === 'number' || type === 'integer') && input.value !== '') return Number(input.value);
          return input.value;
        },
        empty: () => type === 'boolean' ? !input.checked && value === undefined : input.value === '',
      };
    }

    const built = build(rootSchema, initial, '', true, '');
    const form = el('form', 'codex-schema-form');
    const errors = el('div', 'codex-schema-errors'); errors.setAttribute('role', 'alert'); errors.setAttribute('aria-live', 'polite'); errors.hidden = true;
    form.append(errors, built.element);
    form.addEventListener('invalid', event => {
      const input = event.target;
      input.setAttribute('aria-invalid', 'true');
      const label = input.name ? pretty(input.name) : 'This field';
      errors.textContent = label + (input.validity && input.validity.valueMissing ? ' is required.' : ' has an invalid value.');
      errors.hidden = false;
    }, true);
    const clearInvalid = event => {
      const input = event.target;
      if (input && typeof input.checkValidity === 'function' && input.checkValidity()) input.removeAttribute('aria-invalid');
      if (!form.querySelector('[aria-invalid="true"]')) { errors.textContent = ''; errors.hidden = true; }
    };
    form.addEventListener('input', clearInvalid);
    form.addEventListener('change', clearInvalid);
    const validate = () => {
      const messages = validators.map(check => check()).filter(Boolean);
      if (messages.length) {
        errors.textContent = messages.join(' '); errors.hidden = false;
        form.querySelector('[aria-invalid="true"]')?.focus();
        return false;
      }
      return true;
    };
    return { element: form, read: built.read, validate };
  }

  function shapePendingResponse(request, fields) {
    request = request || {}; fields = fields || {};
    const method = String(request.method || '');
    if (/requestUserInput/i.test(method)) {
      if (fields.cancel) return { answers: {} };
      const answers = {};
      Object.entries(fields.answers || {}).forEach(([id, value]) => { answers[id] = { answers: Array.isArray(value) ? value.map(String) : [String(value)] }; });
      return { answers };
    }
    if (/permission/i.test(method)) {
      if (fields.decline || fields.cancel) return { permissions: {}, scope: 'turn' };
      const requested = request.params && request.params.permissions || {};
      const permissions = {};
      Object.entries(fields.permissions || {}).forEach(([key, value]) => { if (Object.prototype.hasOwnProperty.call(requested, key)) permissions[key] = value; });
      return { permissions, scope: fields.scope === 'session' ? 'session' : 'turn' };
    }
    if (/elicitation/i.test(method)) {
      const result = { action: fields.action || (fields.cancel ? 'cancel' : 'decline') };
      if (result.action === 'accept' && fields.content !== undefined) result.content = fields.content;
      return result;
    }
    if (/approval/i.test(method) || /requestApproval/i.test(method)) {
      const decision = fields.decision || 'cancel';
      if (LEGACY_APPROVALS.has(method)) {
        return { decision: ({ accept: 'approved', acceptForSession: 'approved_for_session', decline: 'denied', cancel: 'abort' })[decision] || decision };
      }
      return { decision };
    }
    return fields.result !== undefined ? fields.result : fields;
  }

  async function jsonFetch(url, options) {
    const response = await fetch(url, Object.assign({ cache: 'no-store' }, options || {}));
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Codex returned an unreadable response.'); }
    if (!response.ok || !data || data.ok === false) {
      const error = new Error(conciseError(data && data.error) || 'Request failed.');
      error.payload = data;
      throw error;
    }
    return data;
  }

  function contextBody(context) {
    const out = { thread_id: context && context.threadId || '', repo_path: context && context.repoPath || '' };
    if (context && context.environmentId) out.environment_id = context.environmentId;
    return out;
  }

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return 'action-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  async function establishGeneration(context) {
    if (state.generation !== null && state.generation !== undefined) return state.generation;
    if (state.generationPromise) return state.generationPromise;
    const token = state.requestToken;
    const promise = (async () => {
      const data = await jsonFetch(API + '/operation', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: 'account/read', params: {}, context: contextBody(context), action_id: uuid() }),
      });
      if (data.generation === null || data.generation === undefined) throw new Error('Codex did not provide a current connection receipt.');
      if (token === state.requestToken) state.generation = data.generation;
      return data.generation;
    })();
    state.generationPromise = promise;
    try { return await promise; }
    finally { if (state.generationPromise === promise) state.generationPromise = null; }
  }

  async function runOperation(method, params, context) {
    context = context || state.context || {};
    const lockKey = method + ':' + (context.threadId || '') + ':' + JSON.stringify(params || {});
    if (state.mutationLocks.has(lockKey)) return { skipped: true };
    const descriptor = state.catalog && (state.catalog.methods || []).find(row => row.method === method);
    const mutating = descriptor ? !descriptor.read_only : true;
    const actionId = uuid();
    if (mutating) state.mutationLocks.set(lockKey, actionId);
    const token = state.requestToken;
    try {
      const generation = mutating ? await establishGeneration(context) : null;
      const body = { method, params: params || {}, context: contextBody(context), action_id: actionId };
      if (mutating) body.generation = generation;
      const data = await jsonFetch(API + '/operation', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (token === state.requestToken && data.generation !== null && data.generation !== undefined) state.generation = data.generation;
      if (token === state.requestToken && data.sidebar_sync_pending) showNotice('Codex completed the action; the sidebar update is still pending.');
      if (token === state.requestToken && data.queue_owner_sync_pending) showNotice('Codex completed the queue action; queue ownership reconciliation is pending.');
      if (data.uncertain) throw new Error('Codex could not confirm whether that action completed. Check the conversation before trying again.');
      return data.result === undefined ? data : data.result;
    } catch (error) {
      if (token === state.requestToken && error && error.payload && error.payload.generation !== null && error.payload.generation !== undefined) state.generation = error.payload.generation;
      throw error;
    } finally {
      if (mutating && state.mutationLocks.get(lockKey) === actionId) state.mutationLocks.delete(lockKey);
    }
  }

  async function respond(request, result) {
    if (!request || request.state !== 'pending' || state.responseLocks.has(request.key)) return { skipped: true };
    const receipt = uuid();
    state.responseLocks.set(request.key, receipt);
    try {
      return await jsonFetch(API + '/respond', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: request.key, generation: request.generation || state.generation, result, context: contextBody(state.context) }),
      });
    } finally { if (state.responseLocks.get(request.key) === receipt) state.responseLocks.delete(request.key); }
  }

  function descriptorSurface(descriptor) {
    const group = String(descriptor.group || '').toLowerCase();
    const method = String(descriptor.method || '');
    if (/account|model|config|skill|plugin|market|connector|mcp|app/.test(group + ' ' + method)) return 'settings';
    if (/file|fs\/|review|terminal|environment|sandbox|project|remotecontrol/.test(group + ' ' + method)) return 'workspace';
    return 'conversation';
  }

  function descriptorsFor(surface) {
    const methods = state.catalog && Array.isArray(state.catalog.methods) ? state.catalog.methods : [];
    return methods.filter(row => !row.internal && descriptorSurface(row) === surface);
  }

  function hasRequired(schema) { return !!(schema && Array.isArray(schema.required) && schema.required.length); }
  function hasFormFields(schema) {
    if (!schema || typeof schema !== 'object') return schema === true;
    if (Object.keys(schema.properties || {}).length || schema.additionalProperties) return true;
    return ['oneOf', 'anyOf', 'allOf'].some(key => Array.isArray(schema[key]) && schema[key].length);
  }

  async function getSchema(method) {
    const key = (state.catalog && state.catalog.fingerprint || '') + ':' + (state.catalog && state.catalog.experimental_enabled ? 'preview' : 'stable') + ':' + method;
    if (state.schemas.has(key)) return state.schemas.get(key);
    const data = await jsonFetch(API + '/schema?method=' + encodeURIComponent(method));
    state.schemas.set(key, data.descriptor);
    return data.descriptor;
  }

  function showError(message, root) {
    const target = root || state.root && state.root.querySelector('[data-codex-notices]');
    if (!target) return;
    const notice = el('div', 'codex-client-notice is-error', message);
    target.append(notice);
  }

  function showNotice(message) {
    const target = state.root && state.root.querySelector('[data-codex-notices]');
    if (!target) return;
    const notice = el('div', 'codex-client-notice', message); target.append(notice);
    window.setTimeout(() => notice.remove(), 5000);
  }

  function closeDialog() { state.root && state.root.querySelector('.codex-client-dialog-layer')?.remove(); }

  async function openAction(descriptor) {
    if (!descriptor.available) { showError(descriptor.unavailable_reason || 'This action is unavailable.'); return; }
    let full;
    try { full = await getSchema(descriptor.method); } catch (error) { showError(conciseError(error)); return; }
    if (full.read_only && (full.params_type === 'null' || !hasFormFields(full.params_schema))) {
      await executeAction(full, {}); return;
    }
    const layer = el('div', 'codex-client-dialog-layer');
    const dialog = el('section', 'codex-client-dialog'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true');
    const header = el('header'); header.append(el('div', '', full.title || descriptor.title || pretty(full.method)));
    const close = el('button', 'codex-client-icon-button', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Close'); close.addEventListener('click', closeDialog); header.append(close);
    const contextual = {};
    const properties = full.params_schema && full.params_schema.properties || {};
    Object.keys(properties).forEach(key => {
      if (/^thread_?id$/i.test(key)) contextual[key] = state.context.threadId;
      if (/^(cwd|repo_?path|working_?directory)$/i.test(key)) contextual[key] = state.context.repoPath;
      if (/^environment_?id$/i.test(key) && state.context.environmentId) contextual[key] = state.context.environmentId;
    });
    const built = createForm(full.params_schema || { type: 'object' }, contextual, state.context);
    const footer = el('footer');
    const cancel = el('button', 'codex-client-button is-quiet', 'Cancel'); cancel.type = 'button'; cancel.addEventListener('click', closeDialog);
    const run = el('button', 'codex-client-button is-primary', full.read_only ? 'Open' : 'Run action'); run.type = 'submit';
    footer.append(cancel, run);
    const form = built.element;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!built.validate()) return;
      run.disabled = true;
      try { await executeAction(full, await built.read()); closeDialog(); }
      catch (error) { showError(conciseError(error), dialog); }
      finally { run.disabled = false; }
    });
    dialog.append(header);
    const description = productDescription(full);
    if (description) dialog.append(el('p', 'codex-client-dialog-description', description));
    if (!full.read_only && full.method.startsWith('thread/queue/')) dialog.append(el('p', 'codex-client-dialog-description',
      'This action uses the Codex queue for this task. Send or remove existing CCC queued messages first. You can switch back when the Codex queue is empty.'));
    form.append(footer);
    dialog.append(form); layer.append(dialog); state.root.append(layer);
    form.querySelector('input:not([type=hidden]),textarea,select,button')?.focus();
  }

  async function executeAction(descriptor, params) {
    const token = state.requestToken;
    try {
      if (['thread/archive', 'thread/delete', 'thread/revert', 'thread/rollback'].includes(descriptor.method)) await disposeMedia();
      const result = await runOperation(descriptor.method, params, state.context);
      if (token !== state.requestToken || state.closed) return result;
      state.activeRead = descriptor.read_only ? { descriptor, params } : null;
      showOperationResult(descriptor, result);
      const lifecycleHandled = !descriptor.read_only && await handleOperationLifecycle(descriptor.method, params, result);
      if (!descriptor.read_only && !lifecycleHandled) await loadState();
      return result;
    } catch (error) { if (token === state.requestToken) showError(conciseError(error)); throw error; }
    finally { if (!state.closed && token === state.requestToken) mountMedia(); }
  }

  function threadResult(result) {
    if (!result || typeof result !== 'object') return null;
    const candidate = result.thread || result.data && result.data.thread || result;
    const id = candidate && (candidate.id || candidate.threadId) || result.threadId;
    return id ? Object.assign({}, candidate, { id }) : null;
  }

  function emitThreadLifecycle(method, params, thread) {
    const detail = {
      method,
      threadId: thread && thread.id || params && params.threadId || state.context && state.context.threadId || '',
      previousThreadId: state.context && state.context.threadId || '',
      thread: thread || null,
    };
    window.dispatchEvent(new CustomEvent('ccc:codex-lifecycle', { detail }));
    if (typeof window.CCCCodexClientLifecycle === 'function') {
      try { window.CCCCodexClientLifecycle(detail); } catch (_) {}
    }
    return detail;
  }

  async function handleOperationLifecycle(method, params, result) {
    const createsThread = method === 'thread/start' || method === 'thread/fork';
    if (createsThread) {
      const thread = threadResult(result);
      if (!thread) return false;
      const prior = Object.assign({}, state.context);
      emitThreadLifecycle(method, params, thread);
      await open(Object.assign(prior, {
        threadId: thread.id,
        repoPath: thread.cwd || thread.repoPath || prior.repoPath,
        title: thread.name || thread.title || prior.title,
      }));
      showNotice(method === 'thread/fork' ? 'Opened the forked task.' : 'Opened the new task.');
      return true;
    }
    if (['thread/name/set', 'thread/archive', 'thread/unarchive', 'thread/delete'].includes(method)) {
      emitThreadLifecycle(method, params, null);
      if (method === 'thread/archive' || method === 'thread/delete') {
        close();
        return true;
      }
    }
    return false;
  }

  function showOperationResult(descriptor, result) {
    const panel = state.root && state.root.querySelector('[data-codex-result]');
    if (!panel) return;
    panel.replaceChildren();
    const header = el('div', 'codex-client-result-header'); header.append(el('strong', '', descriptor.title || pretty(descriptor.method)));
    const close = el('button', 'codex-client-icon-button', '×'); close.addEventListener('click', () => { state.activeRead = null; panel.hidden = true; panel.replaceChildren(); }); header.append(close);
    panel.append(header);
    if (result && typeof result === 'object') panel.append(keyValueView(result));
    else panel.append(el('p', '', valueSummary(result) || 'Completed.'));
    panel.hidden = false;
  }

  function actionCard(descriptor) {
    const card = el('button', 'codex-client-action'); card.type = 'button'; card.dataset.method = descriptor.method;
    if (!descriptor.available) card.classList.add('is-unavailable');
    const top = el('span', 'codex-client-action-top');
    top.append(el('strong', '', descriptor.title || pretty(descriptor.method)));
    if (descriptor.experimental) top.append(el('span', 'codex-client-badge', 'Preview'));
    card.append(top, el('span', 'codex-client-action-meta', descriptor.available ? (descriptor.read_only ? 'View' : 'Action') : descriptor.unavailable_reason || 'Unavailable'));
    card.addEventListener('click', () => openAction(descriptor));
    return card;
  }

  function renderCatalog() {
    const host = state.root && state.root.querySelector('[data-codex-catalog]');
    if (!host) return;
    host.replaceChildren();
    const all = descriptorsFor(state.activeSurface);
    const query = state.query.trim().toLowerCase();
    const groups = new Map();
    all.forEach(descriptor => {
      const haystack = (descriptor.title + ' ' + descriptor.group + ' ' + descriptor.method).toLowerCase();
      if (query && !haystack.includes(query)) return;
      const group = descriptor.group || (state.activeSurface === 'conversation' ? 'Conversation' : pretty(state.activeSurface));
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(descriptor);
    });
    const ordered = Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
    syncToolsToggle();
    if (!ordered.length) { host.append(el('div', 'codex-client-empty', state.catalog ? 'No actions match this search.' : 'Loading available actions…')); return; }
    ordered.forEach(([group, methods]) => {
      const section = el('section', 'codex-client-action-group');
      const heading = el('h3', '', pretty(group)); section.append(heading);
      const grid = el('div', 'codex-client-action-grid');
      methods.sort((a, b) => {
        const ar = COMMON_METHODS.indexOf(a.method); const br = COMMON_METHODS.indexOf(b.method);
        if (ar >= 0 || br >= 0) return (ar < 0 ? 999 : ar) - (br < 0 ? 999 : br);
        return String(a.title).localeCompare(String(b.title));
      }).forEach(descriptor => grid.append(actionCard(descriptor)));
      section.append(grid); host.append(section);
    });
  }

  function syncToolsToggle() {
    if (!state.root) return;
    const toggle = state.root.querySelector('[data-codex-tools-toggle]');
    if (!toggle) return;
    const count = descriptorsFor('conversation').length;
    toggle.textContent = 'Tools' + (state.catalog ? ' (' + count + ')' : '');
    toggle.hidden = state.activeSurface !== 'conversation';
    toggle.setAttribute('aria-expanded', state.toolsOpen ? 'true' : 'false');
    state.root.classList.toggle('is-tools-collapsed', !state.toolsOpen);
  }

  function renderTurns() {
    const host = state.root && state.root.querySelector('[data-codex-transcript]');
    if (!host) return;
    const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 100;
    const openKeys = new Set(Array.from(host.querySelectorAll('details[open][data-item-key]')).map(node => node.dataset.itemKey));
    host.replaceChildren();
    if (state.historyCursor) {
      const older = el('button', 'codex-client-load-earlier', 'Load earlier messages'); older.type = 'button'; older.addEventListener('click', loadEarlier); host.append(older);
    }
    const turns = state.thread && Array.isArray(state.thread.turns) ? state.thread.turns : [];
    if (!turns.length) host.append(el('div', 'codex-client-empty', state.connected ? 'Start the conversation below.' : 'Connecting to this task…'));
    turns.forEach(turn => {
      const section = el('section', 'codex-client-turn'); section.dataset.turnId = turn.id || '';
      (turn.items || []).forEach(item => {
        const node = renderItem(item);
        if (node.matches('details') && openKeys.has(node.dataset.itemKey)) node.open = true;
        section.append(node);
      });
      if (turn.plan && !(turn.items || []).some(item => itemKind(item).toLowerCase() === 'plan')) section.append(renderItem({ type: 'plan', id: (turn.id || '') + '-plan', plan: turn.plan }));
      if (turn.diff && !(turn.items || []).some(item => /diff|filechange/i.test(itemKind(item)))) section.append(renderItem({ type: 'diff', id: (turn.id || '') + '-diff', diff: turn.diff }));
      host.append(section);
    });
    if (state.activity.length) {
      const recent = el('details', 'codex-client-event-log'); recent.append(el('summary', '', 'Recent activity'));
      state.activity.slice(-12).forEach(event => recent.append(
        el('div', '', pretty(event.method) + (event.truncated ? ' · More detail available in task state' : ''))
      ));
      host.append(recent);
    }
    if (nearBottom) host.scrollTop = host.scrollHeight;
  }

  function questionEntries(params) {
    const questions = params && (params.questions || params.items || params.question) || [];
    return Array.isArray(questions) ? questions : [questions];
  }

  function approvalContextView(params) {
    const view = el('div', 'codex-client-approval-context');
    const command = params.command || params.cmd;
    if (command) {
      view.append(el('div', 'codex-client-context-label', 'Command'));
      view.append(el('pre', 'codex-client-approval-command', Array.isArray(command) ? command.join(' ') : String(command)));
    }
    if (params.cwd) {
      const row = el('div', 'codex-client-context-row'); row.append(el('strong', '', 'Workspace'), el('span', '', String(params.cwd))); view.append(row);
    }
    if (params.environmentId) {
      const row = el('div', 'codex-client-context-row'); row.append(el('strong', '', 'Environment'), el('span', '', String(params.environmentId))); view.append(row);
    }
    if (params.kind) {
      const row = el('div', 'codex-client-context-row'); row.append(el('strong', '', 'Action'), el('span', '', pretty(params.kind))); view.append(row);
    }
    if (params.grantRoot) {
      const row = el('div', 'codex-client-context-row'); row.append(el('strong', '', 'Requested write access'), el('span', '', String(params.grantRoot))); view.append(row);
    }
    const commandActions = params.commandActions || params.parsedCmd;
    if (Array.isArray(commandActions) && commandActions.length) {
      const details = el('details', 'codex-client-approval-details'); details.open = true;
      details.append(el('summary', '', 'Command details'));
      commandActions.forEach(action => details.append(keyValueView(action)));
      view.append(details);
    }
    const fileChanges = params.fileChanges || params.changes;
    if (fileChanges && typeof fileChanges === 'object') {
      const entries = Array.isArray(fileChanges) ? fileChanges.map((value, index) => [String(index + 1), value]) : Object.entries(fileChanges);
      const details = el('details', 'codex-client-approval-details'); details.open = true;
      details.append(el('summary', '', 'File changes (' + entries.length + ')'));
      entries.forEach(([filePath, change]) => {
        const file = el('div', 'codex-client-approval-file'); file.append(el('strong', '', filePath));
        if (change && typeof change === 'object') {
          if (change.type) file.append(el('span', 'codex-client-badge', pretty(change.type)));
          const patch = change.unified_diff || change.diff || change.patch || change.content;
          if (patch) file.append(el('pre', 'codex-client-diff', String(patch)));
          file.append(keyValueView(change, ['type', 'unified_diff', 'diff', 'patch', 'content']));
        } else file.append(el('span', '', valueSummary(change)));
        details.append(file);
      });
      view.append(details);
    }
    const contextFields = [
      ['additionalPermissions', 'Additional permissions'], ['permissions', 'Requested permissions'],
      ['networkPolicy', 'Network policy'], ['networkPolicyAmendment', 'Network policy change'],
      ['networkApprovalContext', 'Network approval context'],
      ['proposedExecpolicyAmendment', 'Command policy change'], ['execpolicyAmendment', 'Command policy change'],
    ];
    contextFields.forEach(([key, title]) => {
      if (params[key] === undefined || params[key] === null) return;
      const details = el('details', 'codex-client-approval-details'); details.open = true;
      details.append(el('summary', '', title));
      if (typeof params[key] === 'object') details.append(keyValueView(params[key]));
      else details.append(el('p', '', valueSummary(params[key])));
      view.append(details);
    });
    return view.childNodes.length ? view : null;
  }

  function renderPendingRequest(request) {
    const card = el('section', 'codex-client-request'); card.dataset.requestKey = request.key;
    const method = String(request.method || '');
    const params = request.params || {};
    card.append(el('h3', '', /approval|permission/i.test(method) ? 'Approval needed' : /elicitation/i.test(method) ? 'More information needed' : 'Codex has a question'));
    if (params.reason || params.message || params.description) card.append(markdownNode(params.reason || params.message || params.description));
    if (/approval|requestApproval/i.test(method)) {
      const contextView = approvalContextView(params);
      if (contextView) card.append(contextView);
    }
    const submit = async fields => {
      card.classList.add('is-submitting');
      try { await respond(request, shapePendingResponse(request, fields)); await loadState(); }
      catch (error) { showError(conciseError(error), card); card.classList.remove('is-submitting'); }
    };
    if (/requestUserInput/i.test(method)) {
      const answers = {};
      questionEntries(params).forEach((question, index) => {
        if (!question || typeof question !== 'object') return;
        const id = question.id || question.questionId || String(index);
        const field = el('fieldset', 'codex-client-question'); field.append(el('legend', '', question.header || question.title || question.question || 'Question'));
        if (question.header && question.question) field.append(el('p', '', question.question));
        const options = question.options || question.choices || [];
        if (Array.isArray(options) && options.length) {
          options.forEach(option => {
            const label = el('label', 'codex-client-choice'); const input = el('input'); input.type = question.multiSelect ? 'checkbox' : 'radio'; input.name = 'question-' + id; input.value = typeof option === 'string' ? option : option.value || option.label || option.id;
            label.append(input, el('span', '', typeof option === 'string' ? option : option.label || option.value)); if (option.description) label.append(el('small', '', option.description)); field.append(label);
          });
        }
        const free = el('input', 'codex-schema-input'); free.type = question.secret || question.isSecret ? 'password' : 'text'; free.placeholder = question.placeholder || 'Type an answer'; field.append(free);
        answers[id] = () => {
          const picked = Array.from(field.querySelectorAll('input[type=radio]:checked,input[type=checkbox]:checked')).map(input => input.value);
          if (free.value.trim()) picked.push(free.value.trim());
          return picked;
        };
        card.append(field);
      });
      const actions = el('div', 'codex-client-request-actions');
      const cancel = el('button', 'codex-client-button is-quiet', 'Cancel'); cancel.addEventListener('click', () => submit({ cancel: true }));
      const send = el('button', 'codex-client-button is-primary', 'Send answers'); send.addEventListener('click', () => submit({ answers: Object.fromEntries(Object.entries(answers).map(([id, read]) => [id, read()])) }));
      actions.append(cancel, send); card.append(actions);
    } else if (/permission/i.test(method)) {
      const requested = params.permissions || {};
      const permissions = {};
      const list = el('div', 'codex-client-permissions');
      Object.entries(requested).forEach(([key, value]) => {
        const label = el('label', 'codex-client-choice'); const input = el('input'); input.type = 'checkbox'; input.checked = true;
        label.append(input, el('span', '', pretty(key)), el('small', '', valueSummary(value))); list.append(label); permissions[key] = { input, value };
      });
      const scope = el('select', 'codex-schema-input'); scope.append(new Option('This turn', 'turn'), new Option('This session', 'session'));
      const actions = el('div', 'codex-client-request-actions');
      const deny = el('button', 'codex-client-button is-quiet', 'Decline'); deny.addEventListener('click', () => submit({ decline: true }));
      const allow = el('button', 'codex-client-button is-primary', 'Allow selected'); allow.addEventListener('click', () => submit({ permissions: Object.fromEntries(Object.entries(permissions).filter(([, entry]) => entry.input.checked).map(([key, entry]) => [key, entry.value])), scope: scope.value }));
      actions.append(deny, scope, allow); card.append(list, actions);
    } else if (/elicitation/i.test(method)) {
      const url = safeUrl(params.url, false);
      if (url) { const link = el('a', 'codex-client-safe-link', 'Open secure sign-in'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); }
      let built = null;
      if (params.requestedSchema) { built = createForm(params.requestedSchema, {}, state.context); card.append(built.element); }
      const actions = el('div', 'codex-client-request-actions');
      ['cancel', 'decline', 'accept'].forEach(action => {
        const button = el('button', 'codex-client-button ' + (action === 'accept' ? 'is-primary' : 'is-quiet'), action === 'accept' ? (url ? 'I completed it' : 'Continue') : pretty(action));
        button.type = action === 'accept' && built ? 'submit' : 'button';
        if (!(action === 'accept' && built)) button.addEventListener('click', () => submit({ action }));
        actions.append(button);
      });
      if (built) {
        built.element.addEventListener('submit', async event => {
          event.preventDefault();
          if (!built.validate()) return;
          await submit({ action: 'accept', content: await built.read() });
        });
        built.element.append(actions);
      } else card.append(actions);
    } else {
      const rawDecisions = params.availableDecisions || params.decisions || ['accept', 'decline', 'cancel'];
      const decisions = Array.isArray(rawDecisions) ? rawDecisions : Object.keys(rawDecisions);
      const actions = el('div', 'codex-client-request-actions');
      decisions.forEach(choice => {
        const structured = !!choice && typeof choice === 'object';
        const structuredKey = structured ? Object.keys(choice)[0] : '';
        const decision = structured ? choice : choice;
        if (!decision || structured && !structuredKey) return;
        const label = structured ? pretty(structuredKey) : pretty(choice);
        const button = el('button', 'codex-client-button ' + (/accept|approve/i.test(decision) ? 'is-primary' : 'is-quiet'), label);
        if (structured) {
          button.dataset.structuredDecision = 'true';
          button.append(el('small', '', valueSummary(choice[structuredKey])));
        }
        button.addEventListener('click', () => submit({ decision })); actions.append(button);
      });
      card.append(actions);
    }
    return card;
  }

  function renderRequests() {
    const host = state.root && state.root.querySelector('[data-codex-requests]');
    if (!host) return;
    host.replaceChildren();
    const pending = (state.requests || []).filter(request => request && request.state === 'pending');
    host.hidden = pending.length === 0;
    pending.forEach(request => host.append(renderPendingRequest(request)));
  }

  function findMethod(candidates) {
    const methods = state.catalog && state.catalog.methods || [];
    for (const candidate of candidates) {
      const found = methods.find(row => row.method === candidate && row.available);
      if (found) return found;
    }
    return null;
  }

  function runningTurn() {
    const turns = state.thread && state.thread.turns || [];
    return turns.slice().reverse().find(turn => /progress|running|active/i.test(String(turn.status || '')));
  }

  async function sendComposer(text, attachment) {
    const threadId = state.context && state.context.threadId;
    const running = runningTurn();
    const descriptor = running ? findMethod(['turn/steer', 'turn/steerInput']) : findMethod(['turn/start']);
    if (!descriptor) throw new Error(running ? 'Steering is not available in this Codex version.' : 'Sending messages is not available in this Codex version.');
    const full = await getSchema(descriptor.method);
    const props = full.params_schema && full.params_schema.properties || {};
    const params = {};
    const inputs = [];
    if (text) inputs.push({ type: 'text', text });
    if (attachment) inputs.push({ type: 'image', url: attachment.url });
    const modelSelect = state.root && state.root.querySelector('[data-codex-model]');
    const effortSelect = state.root && state.root.querySelector('[data-codex-effort]');
    Object.keys(props).forEach(key => {
      if (/^thread_?id$/i.test(key)) params[key] = threadId;
      else if (/^(?:turn_?id|expectedTurnId)$/i.test(key) && running) params[key] = running.id;
      else if (/^(input|items|content)$/i.test(key)) params[key] = inputs;
      else if (/^(message|text|prompt)$/i.test(key)) params[key] = text;
      else if (/^model$/i.test(key) && !running && modelSelect && modelSelect.value) params[key] = modelSelect.value;
      else if (/^(effort|reasoningEffort)$/i.test(key) && !running && effortSelect && effortSelect.value) params[key] = effortSelect.value;
    });
    if (!Object.keys(params).some(key => /input|items|content|message|text|prompt/i.test(key))) throw new Error('This Codex version needs additional send fields. Open the full action form from Conversation.');
    return executeAction(full, params);
  }

  function wireComposer(root) {
    const form = root.querySelector('[data-codex-composer]');
    const input = form.querySelector('textarea');
    const interrupt = form.querySelector('[data-codex-interrupt]');
    const modelSelect = form.querySelector('[data-codex-model]');
    const effortSelect = form.querySelector('[data-codex-effort]');
    const imageInput = form.querySelector('[data-codex-image-input]');
    const attachment = form.querySelector('[data-codex-attachment]');
    const attachmentName = attachment.querySelector('[data-codex-attachment-name]');
    const clearAttachment = () => {
      state.composerAttachment = null;
      imageInput.value = '';
      attachment.hidden = true;
      attachmentName.textContent = '';
    };
    const syncEfforts = () => {
      const selected = state.composerModels.find(model => (model.id || model.model) === modelSelect.value);
      const efforts = selected && Array.isArray(selected.supportedReasoningEfforts) ? selected.supportedReasoningEfforts : [];
      effortSelect.replaceChildren();
      if (!efforts.length) { effortSelect.hidden = true; return; }
      efforts.forEach(entry => {
        const value = typeof entry === 'string' ? entry : entry.reasoningEffort || entry.effort;
        if (!value) return;
        const option = el('option', '', pretty(value)); option.value = value; option.title = typeof entry === 'object' ? entry.description || '' : '';
        effortSelect.append(option);
      });
      effortSelect.value = selected.defaultReasoningEffort || effortSelect.options[0]?.value || '';
      effortSelect.hidden = false;
    };
    const syncOptions = () => {
      const previous = modelSelect.value;
      modelSelect.replaceChildren();
      const automatic = el('option', '', 'Default model'); automatic.value = ''; modelSelect.append(automatic);
      state.composerModels.forEach(model => {
        const id = model.id || model.model; if (!id) return;
        const option = el('option', '', model.displayName || model.name || id); option.value = id; option.title = model.description || '';
        modelSelect.append(option);
      });
      modelSelect.value = state.composerModels.some(model => (model.id || model.model) === previous) ? previous : '';
      modelSelect.hidden = state.composerModels.length === 0;
      syncEfforts();
    };
    state.composerOptionsSync = syncOptions;
    modelSelect.addEventListener('change', syncEfforts);
    imageInput.addEventListener('change', () => {
      const file = imageInput.files && imageInput.files[0];
      if (!file) { clearAttachment(); return; }
      if (!String(file.type || '').startsWith('image/')) { clearAttachment(); showError('Choose an image file.'); return; }
      if (file.size > MAX_COMPOSER_IMAGE_BYTES) { clearAttachment(); showError('Image must be smaller than 700 KB.'); return; }
      const reader = new FileReader();
      reader.onload = () => {
        const url = String(reader.result || '');
        if (!url.startsWith('data:image/') || url.length > 1000000) { clearAttachment(); showError('That image is too large to attach safely.'); return; }
        state.composerAttachment = { name: file.name, size: file.size, url };
        attachmentName.textContent = file.name;
        attachment.hidden = false;
      };
      reader.onerror = () => { clearAttachment(); showError('Could not read that image.'); };
      reader.readAsDataURL(file);
    });
    attachment.querySelector('[data-codex-attachment-remove]').addEventListener('click', clearAttachment);
    const sync = () => {
      const running = runningTurn();
      form.querySelector('[data-codex-send-label]').textContent = running ? 'Steer' : 'Send';
      interrupt.hidden = !running;
      modelSelect.disabled = !!running;
      effortSelect.disabled = !!running;
    };
    state.composerSync = sync;
    sync();
    form.addEventListener('submit', async event => {
      event.preventDefault(); const text = input.value.trim(); if (!text && !state.composerAttachment) return;
      const selectedModel = state.composerModels.find(model => (model.id || model.model) === modelSelect.value);
      if (state.composerAttachment && selectedModel && Array.isArray(selectedModel.inputModalities) && !selectedModel.inputModalities.includes('image')) {
        showError('The selected model does not accept images.'); return;
      }
      const button = form.querySelector('[type=submit]'); button.disabled = true;
      try { await sendComposer(text, state.composerAttachment); input.value = ''; clearAttachment(); }
      catch (error) { showError(conciseError(error)); }
      finally { button.disabled = false; sync(); }
    });
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
    interrupt.addEventListener('click', async () => {
      const descriptor = findMethod(['turn/interrupt']); const turn = runningTurn();
      if (!descriptor || !turn) return;
      try {
        const full = await getSchema(descriptor.method); const props = full.params_schema && full.params_schema.properties || {}; const params = {};
        Object.keys(props).forEach(key => { if (/^thread_?id$/i.test(key)) params[key] = state.context.threadId; if (/^turn_?id$/i.test(key)) params[key] = turn.id; });
        await executeAction(full, params);
      } catch (error) { showError(conciseError(error)); }
    });
    syncOptions();
  }

  async function loadComposerModels(token) {
    const descriptor = state.catalog && (state.catalog.methods || []).find(row => row.method === 'model/list' && row.available);
    if (!descriptor || state.closed) return;
    try {
      const result = await runOperation('model/list', { limit: 100 }, state.context);
      if (state.closed || token !== state.requestToken) return;
      const models = Array.isArray(result) ? result : result && (result.data || result.models);
      state.composerModels = Array.isArray(models) ? models.filter(model => model && !model.hidden) : [];
      state.composerOptionsSync?.();
    } catch (_) {
      state.composerModels = [];
      state.composerOptionsSync?.();
    }
  }

  function mount(context) {
    const pane = context.paneEl || document.querySelector('.conv-pane.is-codex-session');
    if (!pane) throw new Error('Open a Codex conversation first.');
    const root = el('section', 'codex-client-shell is-tools-collapsed'); root.dataset.codexClient = 'true'; root.dataset.surface = state.activeSurface;
    root.innerHTML = '<header class="codex-client-topbar">'
      + '<div class="codex-client-title"><span class="codex-client-mark">⌘</span><div><strong>Codex workspace</strong><span data-codex-context></span></div></div>'
      + '<nav class="codex-client-tabs" aria-label="Codex workspace sections">'
      + '<button type="button" data-surface="conversation" aria-current="page">Conversation</button>'
      + '<button type="button" data-surface="workspace">Workspace</button>'
      + '<button type="button" data-surface="settings">Settings</button></nav>'
      + '<div class="codex-client-top-actions"><button type="button" class="codex-client-button is-quiet codex-client-tools-toggle" data-codex-tools-toggle aria-expanded="false">Tools</button>'
      + '<button type="button" class="codex-client-icon-button" data-codex-close aria-label="Close Codex workspace">×</button></div></header>'
      + '<div class="codex-client-notices" data-codex-notices aria-live="polite"></div>'
      + '<main class="codex-client-main">'
      + '<section class="codex-client-conversation" data-codex-conversation>'
      + '<div class="codex-client-requests" data-codex-requests hidden></div>'
      + '<div class="codex-client-transcript" data-codex-transcript></div>'
      + '<form class="codex-client-composer" data-codex-composer><div class="codex-client-composer-options">'
      + '<select data-codex-model aria-label="Model" title="Model for the next turn" hidden></select><select data-codex-effort aria-label="Reasoning effort" title="Reasoning effort for the next turn" hidden></select>'
      + '<label class="codex-client-attach" title="Attach one image smaller than 700 KB" aria-label="Attach image">＋<input type="file" accept="image/*" data-codex-image-input></label></div>'
      + '<span class="codex-client-attachment" data-codex-attachment hidden><span data-codex-attachment-name></span><button type="button" data-codex-attachment-remove aria-label="Remove image">×</button></span>'
      + '<textarea aria-label="Message Codex" placeholder="Message Codex…" rows="1"></textarea>'
      + '<button type="button" class="codex-client-button is-quiet" data-codex-interrupt hidden>Stop</button>'
      + '<button type="submit" class="codex-client-button is-primary"><span data-codex-send-label>Send</span> <span aria-hidden="true">↑</span></button></form></section>'
      + '<aside class="codex-client-tools"><div class="codex-client-toolhead"><div><strong data-codex-tool-title>Conversation tools</strong><span>Everything available in this Codex version</span></div>'
      + '<input type="search" data-codex-search aria-label="Search actions" placeholder="Find an action"></div>'
      + '<div data-codex-media-host></div><div class="codex-client-catalog" data-codex-catalog></div></aside></main>'
      + '<aside class="codex-client-result" data-codex-result hidden></aside>'
      + '<footer class="codex-client-footer"><span data-codex-connection>Connecting…</span><span data-codex-usage></span><label>Message queue <select data-codex-queue-owner><option value="ccc">CCC</option><option value="native">Codex</option></select></label><label><input type="checkbox" data-codex-preview> Preview features</label></footer>';
    pane.classList.add('codex-client-open');
    Array.from(pane.children).forEach(child => {
      if (child === root || child.matches('.conv-pane-header')) return;
      state.previousDisplay.set(child, child.style.display);
      child.style.display = 'none';
    });
    pane.append(root);
    root.querySelector('[data-codex-context]').textContent = (context.title || 'Selected task') + (context.repoPath ? ' · ' + context.repoPath.split('/').filter(Boolean).pop() : '');
    root.querySelector('[data-codex-close]').addEventListener('click', close);
    root.querySelector('[data-codex-tools-toggle]').addEventListener('click', () => { state.toolsOpen = !state.toolsOpen; syncToolsToggle(); });
    root.querySelectorAll('[data-surface]').forEach(button => button.addEventListener('click', () => {
      state.activeSurface = button.dataset.surface;
      root.dataset.surface = state.activeSurface;
      root.querySelectorAll('[data-surface]').forEach(other => other.setAttribute('aria-current', String(other === button ? 'page' : 'false')));
      root.querySelector('[data-codex-tool-title]').textContent = pretty(state.activeSurface) + ' tools';
      root.querySelector('[data-codex-conversation]').hidden = state.activeSurface !== 'conversation';
      renderCatalog(); syncToolsToggle();
    }));
    root.querySelector('[data-codex-search]').addEventListener('input', event => { state.query = event.target.value; renderCatalog(); });
    root.querySelector('[data-codex-queue-owner]').addEventListener('change', async event => {
      const select = event.target;
      select.disabled = true;
      try {
        await jsonFetch(API + '/queue-owner', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({owner:select.value, context:contextBody(state.context)})});
        await loadState();
      } catch (error) { showError(conciseError(error)); await loadState().catch(() => {}); }
      finally { select.disabled = false; }
    });
    root.querySelector('[data-codex-preview]').addEventListener('change', async event => {
      event.target.disabled = true;
      try {
        await disposeMedia();
        state.catalog = await jsonFetch(API + '/preferences', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ experimental: event.target.checked }) });
        state.schemas.clear(); renderCatalog(); mountMedia();
      } catch (error) { event.target.checked = !event.target.checked; showError(conciseError(error)); }
      finally { event.target.disabled = false; }
    });
    wireComposer(root);
    return root;
  }

  function setSnapshot(data, includeHistoryCursor) {
    if (!data) return;
    if (data.thread && data.thread.deleted && state.context && data.thread.id === state.context.threadId) {
      emitThreadLifecycle('thread/deleted', { threadId: data.thread.id }, data.thread);
      if (typeof window.showOpToast === 'function') window.showOpToast('This Codex task was deleted.', 'error');
      else window.dispatchEvent(new CustomEvent('ccc:codex-notice', { detail: { message: 'This Codex task was deleted.', type: 'error' } }));
      close();
      return;
    }
    if (data.queue_owner && state.root) state.root.querySelector('[data-codex-queue-owner]').value = data.queue_owner;
    state.generation = data.generation !== undefined ? data.generation : state.generation;
    state.eventCursor = data.cursor !== undefined ? data.cursor : state.eventCursor;
    state.connected = !!data.connected;
    if (data.thread !== undefined) state.thread = data.thread;
    if (Array.isArray(data.requests)) state.requests = data.requests;
    if (includeHistoryCursor) state.historyCursor = data.next_cursor || null;
    updateChrome(); renderTurns(); renderRequests(); state.composerSync?.();
  }

  function updateChrome() {
    if (!state.root) return;
    const connection = state.root.querySelector('[data-codex-connection]');
    connection.textContent = state.connected ? 'Connected' : 'Reconnecting…';
    connection.classList.toggle('is-connected', state.connected);
    const preview = state.root.querySelector('[data-codex-preview]');
    if (preview && state.catalog) preview.checked = !!state.catalog.experimental_enabled;
    const usage = state.root.querySelector('[data-codex-usage]');
    const turns = state.thread && state.thread.turns || [];
    let tokens = 0;
    turns.forEach(turn => { tokens += Number(turn.usage && (turn.usage.totalTokens || turn.usage.total_tokens) || 0); });
    usage.textContent = tokens ? tokens.toLocaleString() + ' tokens in this view' : '';
  }

  async function loadCatalog(token) {
    const catalog = await jsonFetch(API + '/catalog');
    if (token !== state.requestToken || state.closed) return;
    state.catalog = catalog; renderCatalog(); updateChrome(); await loadComposerModels(token);
  }

  async function loadHistory(token, cursor) {
    const transcript = cursor && state.root && state.root.querySelector('[data-codex-transcript]');
    const anchor = transcript ? { top: transcript.scrollTop, height: transcript.scrollHeight } : null;
    const params = new URLSearchParams({ thread_id: state.context.threadId, repo_path: state.context.repoPath });
    if (cursor) params.set('cursor', cursor);
    const data = await jsonFetch(API + '/history?' + params.toString());
    if (token !== state.requestToken || state.closed) return;
    if (cursor && data.thread && state.thread) {
      const older = data.thread.turns || []; const current = state.thread.turns || [];
      data.thread.turns = older.concat(current.filter(turn => !older.some(old => old.id && old.id === turn.id)));
    }
    setSnapshot(data, true);
    if (anchor && transcript.isConnected) {
      transcript.scrollTop = anchor.top + Math.max(0, transcript.scrollHeight - anchor.height);
    }
  }

  async function loadEarlier() {
    if (!state.historyCursor || state.closed) return;
    const cursor = state.historyCursor;
    try { await loadHistory(state.requestToken, cursor); }
    catch (error) { showError('Could not load earlier messages: ' + conciseError(error)); }
  }

  async function loadState() {
    if (state.closed) return;
    const token = state.requestToken;
    const params = new URLSearchParams({ thread_id: state.context.threadId, repo_path: state.context.repoPath });
    const data = await jsonFetch(API + '/state?' + params.toString());
    if (state.closed || token !== state.requestToken) return;
    if (!data.thread) await loadHistory(state.requestToken, null);
    else setSnapshot(data, false);
  }

  function handleEvents(events) {
    if (!Array.isArray(events)) return;
    if (state.mediaController) state.mediaController.handleEvents(events);
    events.forEach(event => {
      if (!event || typeof event !== 'object') return;
      if (event.seq !== undefined) state.eventCursor = event.seq;
      state.activity.push(event);
      if (/terminal|realtime|image|media/i.test(event.method || '')) {
        window.dispatchEvent(new CustomEvent('ccc:codex-stream-event', { detail: event }));
      }
      if (event.method === 'account/login/completed') {
        const success = !event.params || event.params.success !== false;
        showNotice(success ? 'Signed in to Codex.' : 'Codex sign-in did not complete.');
        window.dispatchEvent(new CustomEvent('ccc:codex-account-changed', { detail: event.params || {} }));
      }
    });
    if (state.activity.length > 40) state.activity.splice(0, state.activity.length - 40);
    renderTurns();
    scheduleReadRefresh(events);
    window.dispatchEvent(new CustomEvent('ccc:codex-events', { detail: { context: state.context, events } }));
  }

  function readResource(method) {
    return String(method || '').replace(/\/(?:read|get|list)$/i, '');
  }

  function eventMatchesRead(eventMethod, readMethod) {
    const resource = readResource(readMethod);
    const changed = String(eventMethod || '').replace(/\/(?:updated|changed|completed)$/i, '');
    return !!resource && (changed === resource || changed.startsWith(resource + '/') || resource.startsWith(changed + '/'));
  }

  function scheduleReadRefresh(events) {
    const active = state.activeRead;
    if (!active || !active.descriptor || !active.descriptor.read_only) return;
    if (!events.some(event => event && eventMatchesRead(event.method, active.descriptor.method))) return;
    window.clearTimeout(state.readRefreshTimer);
    state.readRefreshTimer = window.setTimeout(refreshActiveRead, 120);
  }

  async function refreshActiveRead() {
    state.readRefreshTimer = null;
    const active = state.activeRead;
    if (!active || state.closed || !state.root) return;
    if (state.readRefreshInFlight) { state.readRefreshQueued = true; return; }
    state.readRefreshInFlight = true;
    try {
      const result = await runOperation(active.descriptor.method, active.params, state.context);
      if (state.activeRead === active && !state.closed) showOperationResult(active.descriptor, result);
    } catch (error) {
      if (!state.closed) showError('Could not update ' + (active.descriptor.title || 'this view') + ': ' + conciseError(error));
    } finally {
      state.readRefreshInFlight = false;
      if (state.readRefreshQueued) { state.readRefreshQueued = false; scheduleReadRefresh([{ method: active.descriptor.method + '/updated' }]); }
    }
  }

  function schedulePoll(delay) {
    window.clearTimeout(state.pollTimer);
    if (state.closed || document.hidden && !mediaActive()) return;
    state.pollTimer = window.setTimeout(pollNow, delay === undefined ? POLL_BASE_MS : delay);
  }

  async function pollNow() {
    if (state.closed || state.pollInFlight || document.hidden && !mediaActive()) return;
    state.pollInFlight = true;
    const token = state.requestToken;
    const params = new URLSearchParams({ thread_id: state.context.threadId, repo_path: state.context.repoPath });
    if (state.eventCursor !== null && state.eventCursor !== undefined) params.set('cursor', String(state.eventCursor));
    if (state.generation !== null && state.generation !== undefined) params.set('generation', String(state.generation));
    const controller = new AbortController(); state.pollAbort = controller;
    try {
      const data = await jsonFetch(API + '/events?' + params.toString(), { signal: controller.signal });
      if (state.closed || token !== state.requestToken) return;
      state.pollFailures = 0; state.connected = !!data.connected;
      if (data.resync_required || data.generation !== undefined && state.generation !== null && data.generation !== state.generation) {
        await loadHistory(state.requestToken, null);
      } else {
        if (data.generation !== undefined) state.generation = data.generation;
        handleEvents(data.events || []);
        if (data.queue_owner && state.root) state.root.querySelector('[data-codex-queue-owner]').value = data.queue_owner;
        if (data.cursor !== undefined) state.eventCursor = data.cursor;
        if (Array.isArray(data.requests)) { state.requests = data.requests; renderRequests(); }
        if ((data.events || []).length) await loadState(); else updateChrome();
      }
    } catch (error) {
      if (token === state.requestToken && error.name !== 'AbortError' && !state.closed) { state.pollFailures++; state.connected = false; updateChrome(); }
    } finally {
      if (state.pollAbort === controller) state.pollAbort = null;
      if (token === state.requestToken) {
        state.pollInFlight = false;
        schedulePoll(Math.min(POLL_MAX_MS, POLL_BASE_MS * Math.pow(2, state.pollFailures)));
      }
    }
  }

  async function open(context) {
    const cleanup = close();
    const closingToken = state.requestToken;
    await cleanup;
    if (closingToken !== state.requestToken) return;
    context = Object.assign({}, typeof window.CCCCodexClientContext === 'function' ? window.CCCCodexClientContext() : {}, context || {});
    if (!context.threadId || !context.repoPath) throw new Error('Open a Codex conversation with a known repository first.');
    state.closed = false; state.context = context; state.requestToken++; state.schemas = new Map();
    state.thread = null; state.requests = []; state.generation = null; state.eventCursor = null; state.historyCursor = null; state.activity = []; state.pollFailures = 0;
    state.root = mount(context);
    state.visibilityHandler = () => { if (document.hidden && !mediaActive()) { state.pollAbort?.abort(); window.clearTimeout(state.pollTimer); } else { loadState().catch(() => {}); schedulePoll(0); } };
    document.addEventListener('visibilitychange', state.visibilityHandler);
    const token = state.requestToken;
    const results = await Promise.allSettled([loadCatalog(token), loadHistory(token, null)]);
    if (state.closed || token !== state.requestToken) return;
    results.forEach(result => { if (result.status === 'rejected') showError(conciseError(result.reason)); });
    mountMedia();
    schedulePoll(0);
    return state.root;
  }

  function close() {
    const cleanup = disposeMedia();
    state.closed = true; state.requestToken++;
    window.clearTimeout(state.pollTimer); state.pollTimer = null;
    window.clearTimeout(state.readRefreshTimer); state.readRefreshTimer = null;
    state.pollAbort?.abort(); state.pollAbort = null; state.pollInFlight = false;
    if (state.visibilityHandler) document.removeEventListener('visibilitychange', state.visibilityHandler);
    state.visibilityHandler = null;
    document.querySelectorAll('.codex-client-shell').forEach(node => node.remove());
    document.querySelectorAll('.codex-client-open').forEach(pane => pane.classList.remove('codex-client-open'));
    state.previousDisplay.forEach((display, node) => { if (node && node.isConnected) node.style.display = display; });
    state.previousDisplay.clear(); state.root = null; state.context = null; state.generationPromise = null;
    state.composerSync = null; state.composerOptionsSync = null; state.composerModels = []; state.composerAttachment = null;
    state.activeRead = null; state.readRefreshInFlight = false; state.readRefreshQueued = false;
    state.activeSurface = 'conversation'; state.activeGroup = ''; state.query = ''; state.toolsOpen = false;
    state.mutationLocks.clear(); state.responseLocks.clear();
    cleanup.catch(error => {
      window.dispatchEvent(new CustomEvent('ccc:codex-media-cleanup-error', {detail:{message:conciseError(error)}}));
    });
    return cleanup;
  }

  function mediaActive() {
    return !!(state.mediaController && typeof state.mediaController.isActive === 'function' && state.mediaController.isActive());
  }

  function disposeMedia() {
    const controller = state.mediaController;
    state.mediaController = null;
    const prior = state.mediaCleanup;
    let current;
    const retiringContext = state.context && Object.assign({}, state.context);
    const retiringGeneration = state.generation;
    const retiringCursor = state.eventCursor;
    const active = controller && typeof controller.isActive === 'function' && controller.isActive();
    try { current = controller ? controller.dispose() : null; }
    catch (error) { current = Promise.reject(error); }
    if (active && retiringContext) {
      const stopDrain = drainRetiringMedia(controller, retiringContext, retiringGeneration, retiringCursor);
      current = Promise.resolve(current).finally(stopDrain);
    }
    state.mediaCleanupBlocked = true;
    const cleanup = Promise.all([prior, current]).then(() => {
      if (state.mediaCleanup === cleanup) state.mediaCleanupBlocked = false;
    });
    state.mediaCleanup = cleanup;
    // DOM close handlers can ignore the Promise; avoid an unhandled rejection
    // while callers that must wait (preview toggles) still receive the error.
    state.mediaCleanup.catch(() => {});
    return state.mediaCleanup;
  }

  function drainRetiringMedia(controller, context, generation, initialCursor) {
    let stopped = false, cursor = initialCursor, timer = null, abort = null;
    const poll = async () => {
      if (stopped) return;
      abort = new AbortController();
      const deadline = window.setTimeout(() => abort?.abort(), 2500);
      try {
        const params = new URLSearchParams({thread_id:context.threadId, repo_path:context.repoPath});
        if (cursor !== null && cursor !== undefined) params.set('cursor', String(cursor));
        if (generation !== null && generation !== undefined) params.set('generation', String(generation));
        const data = await jsonFetch(API + '/events?' + params, {signal:abort.signal});
        if (!stopped && !data.resync_required && data.generation === generation) {
          controller.handleEvents(data.events || []);
          if (data.cursor !== undefined) cursor = data.cursor;
        }
      } catch (_) { /* The controller's bounded native-close deadline fences failure. */ }
      finally {
        window.clearTimeout(deadline);
        if (!stopped) timer = window.setTimeout(poll, 300);
      }
    };
    timer = window.setTimeout(poll, 0);
    return () => { stopped = true; window.clearTimeout(timer); abort?.abort(); };
  }

  function mountMedia() {
    if (state.closed || state.mediaCleanupBlocked || !state.root || !state.catalog || !window.CCCCodexMedia || state.mediaController) return;
    const context = Object.assign({}, state.context);
    try {
      state.mediaController = window.CCCCodexMedia.attach({
        root: state.root.querySelector('[data-codex-media-host]'), context, catalog: state.catalog,
        operation: (method, params) => runOperation(method, params, context),
        error: message => { if (!state.closed) showError(message); },
      });
    } catch (error) { showError('Media controls could not start: ' + conciseError(error)); }
  }

  function ensureLaunchers() {
    document.querySelectorAll('.conv-pane').forEach(pane => {
      let button = pane.querySelector('[data-codex-workspace-launch]');
      if (!pane.classList.contains('is-codex-session')) { button?.remove(); return; }
      if (button) return;
      const actions = pane.querySelector('.conv-pane-actions'); if (!actions) return;
      button = el('button', 'conv-pane-action codex-client-launch', 'Workspace'); button.type = 'button'; button.dataset.codexWorkspaceLaunch = 'true'; button.title = 'Open the full Codex workspace';
      button.addEventListener('click', () => {
        const bridge = typeof window.CCCCodexClientContext === 'function' ? window.CCCCodexClientContext(pane) : { paneEl: pane };
        open(Object.assign({}, bridge, { paneEl: pane })).catch(error => {
          if (typeof window.showOpToast === 'function') window.showOpToast(conciseError(error), 'error');
          else window.alert(conciseError(error));
        });
      });
      actions.prepend(button);
    });
  }

  window.addEventListener('ccc:conversation-selected', event => {
    if (state.closed || !state.context) return;
    const selected = event.detail || {};
    const samePane = selected.paneEl === state.context.paneEl ||
      selected.paneId && selected.paneId === state.context.paneId;
    if (samePane && selected.threadId !== state.context.threadId) close();
  });

  const observer = new MutationObserver(ensureLaunchers);
  const begin = () => { ensureLaunchers(); observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] }); };
  if (document.body) begin(); else document.addEventListener('DOMContentLoaded', begin, { once: true });

  window.CCCCodexClient = {
    open, close, handleEvents,
    __testing: { createForm, shapePendingResponse, renderItem, runOperation, executeAction, pollNow, loadEarlier, state },
  };
})();
