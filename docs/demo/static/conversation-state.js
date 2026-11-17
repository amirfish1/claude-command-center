/* Pane state shared by dashboard and embedded conversation hosts. */
(function (root) {
  'use strict';
  function createPane(id) {
    return {
      id: id,
      conversationId: null,
      lastLine: 0,
      firstLine: 0,
      loadBeforeLine: 0,
      eventSource: null,
      pendingSends: [],
      pendingSendsByConv: {},
      firstUserMsgRendered: false,
      currentToolGroup: null,
      currentToolCount: 0,
      currentSession: { id: null, cwd: null, cwdExists: false, source: null, repoPath: null },
    };
  }
  function createWorkspace() {
    return { orientation: null, panes: [createPane('p1')], activeIndex: 0, ratio: 0.5 };
  }
  function activePane(workspace) {
    return workspace.panes[workspace.activeIndex];
  }
  const legacyFields = Object.freeze({
    currentConversation: 'conversationId',
    convLastLine: 'lastLine',
    convEventSource: 'eventSource',
    _firstUserMsgRendered: 'firstUserMsgRendered',
    _pendingSends: 'pendingSends',
    _currentToolGroup: 'currentToolGroup',
    _currentToolCount: 'currentToolCount',
    currentSession: 'currentSession',
  });
  // Old window properties and explicit controller adapters must point at the
  // same active pane. Read it for each access: split restore replaces panes.
  function bindLegacyProperty(target, workspace, name) {
    const field = legacyFields[name];
    if (!field) throw new Error('Unknown conversation state property: ' + name);
    Object.defineProperty(target, name, {
      configurable: true,
      get() { return activePane(workspace)[field]; },
      set(value) { activePane(workspace)[field] = value; },
    });
  }
  root.CCCConversationState = Object.freeze({ createPane, createWorkspace, activePane, bindLegacyProperty });
})(typeof window === 'undefined' ? globalThis : window);
