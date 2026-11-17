// Older algorithm fixtures select source by neighboring declarations/comments.
// Follow the dashboard's references to the one canonical shared declaration;
// native factory and browser tests separately exercise the live adapters.
const fs = require('node:fs');
const {functionSource, commentsBefore} = require('./conversation-source.cjs');
const normalize = (source, legacy = false) => source
  .replace(legacy ? /\(0, (?:state|helpers)\.([\w$]+)\)(?=\s*\()/g : /$^/g, '$1')
  .replace(legacy ? /\b(?:state|helpers)\.([\w$]+)/g : /$^/g, '$1')
  .replace(/\(0, (?:sharedState|hostServices|deferredServices)\.([\w$]+)\)(?=\s*\()/g, '$1')
  .replace(/\b(?:sharedState|hostServices|deferredServices)\.([\w$]+)/g, '$1');
const vm = require('node:vm');
function variableSource(module, name) {
  const text = fs.readFileSync(require.resolve('../static/conversation-' + module + '.js'), 'utf8');
  const match = new RegExp('^  (?:const|let|var) ' + name + '\\b', 'm').exec(text);
  if (!match) throw new Error('Missing shared variable: ' + name);
  let end = text.indexOf(';', match.index);
  while (end >= 0) {
    const declaration = text.slice(match.index, end + 1);
    try { new vm.Script(declaration); return commentsBefore(text, match.index) + declaration; } catch (_) {}
    end = text.indexOf(';', end + 1);
  }
  throw new Error('Unclosed shared variable: ' + name);
}
const defaults = fs.readFileSync(require.resolve('../static/conversation-defaults.js'), 'utf8');
const expressions = new Map([...defaults.matchAll(/^      ([\w$]+)\(\) \{\n        const \1 = ([\s\S]*?);\n        return \1;\n      }/gm)]
  .map(match => [match[1], normalize(match[2])]));
module.exports = fs.readFileSync(require.resolve('../static/app.js'), 'utf8')
  .replace(/^  \/\/ Shared conversation-([\w-]+)\.js: (variable )?([\w$]+)\.$/gm,
    (reference, module, variable, name) => reference + '\n' + normalize(variable ? variableSource(module, name) : functionSource(name, true), ["selection","events","composer","transport","panes","input"].includes(module)))
  .replace(/^  (initConversationBindings\d+|installConversationInteraction\d+|installConversationView\d+)\(\);$/gm, (_, name) => {
    const declaration = normalize(functionSource(name));
    return declaration.slice(declaration.indexOf('{') + 1, declaration.lastIndexOf('}')).replace(/^(\s*\n)(?=\S)/, '$1  ');
  })
  .replace(/^  ((?:initConversationBindings|installConversationInteraction|installConversationView)\d+)\(\);$/gm,
    (_, name) => { const code=normalize(functionSource(name)); return code.slice(code.indexOf('\n') + 1, code.lastIndexOf('\n')); })
  .replace(/conversationInitializers\.([\w$]+)\(\)/g, (call, name) => expressions.get(name) ?? call);
