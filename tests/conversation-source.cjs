// Source-level unit fixtures follow functions into their canonical shared file.
// Keep adapter references intact: a fixture supplies its live state/services.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const directory = path.join(__dirname, '../static');
const files = ['app.js', 'archive-boot.js', ...fs.readdirSync(directory).filter(name => /^conversation-.*\.js$/.test(name))];
const sources = files.map(name => fs.readFileSync(path.join(directory, name), 'utf8'));
const source = sources.join('\n');
const declarations = new Map();
for (const text of sources) {
  for (const match of text.matchAll(/^([ \t]*)(?:async )?function (\w+)\(/gm)) {
    if (!declarations.has(match[2]) || match[1] === '  ') declarations.set(match[2], {text, match});
  }
}
const cache = new Map();
function functionSource(name, includeComments = false) {
  if (includeComments) return functionComments(name) + functionSource(name);
  if (cache.has(name)) return cache.get(name);
  const declaration = declarations.get(name);
  if (!declaration) throw new Error('Missing fixture function: ' + name);
  const {text, match} = declaration;
  const lineEnd = text.indexOf('\n', match.index);
  let end = lineEnd;
  if (text[lineEnd - 1] !== '}') {
    const closing = new RegExp('^' + match[1] + '}[ \\t]*$', 'gm');
    closing.lastIndex = lineEnd;
    let endMatch;
    while ((endMatch = closing.exec(text))) {
      end = endMatch.index + endMatch[0].length;
      try { new vm.Script(text.slice(match.index, end)); break; } catch (_) {}
    }
    if (!endMatch) throw new Error('Unclosed fixture function: ' + name);
  }
  const result = text.slice(match.index, end);
  cache.set(name, result);
  return result;
}
function commentsBefore(text, offset) {
  const lines = text.slice(0, offset).trimEnd().split('\n');
  const comments = [];
  while (lines.length) {
    const line = lines.at(-1);
    if (!line.trim() || line.trim().startsWith('//')) comments.unshift(lines.pop());
    else if (line.trim().endsWith('*/')) {
      do { comments.unshift(lines.pop()); } while (lines.length && !comments[0].includes('/*'));
    } else break;
  }
  return comments.length ? comments.join('\n') + '\n' : '';
}
function functionComments(name) {
  const declaration = declarations.get(name);
  if (!declaration) throw new Error('Missing fixture function: ' + name);
  return commentsBefore(declaration.text, declaration.match.index);
}
module.exports = { source, functionSource, commentsBefore, functions: names => names.map(name => functionSource(name)).join('\n') };
