Fixed a duplicate variable declaration in `app.js` that was a hard SyntaxError — it broke parsing of the entire file, so every dashboard load hung forever on "Loading conversations…".
