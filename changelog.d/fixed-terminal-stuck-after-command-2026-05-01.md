**Terminal panel: input no longer stuck after one command.** The
`/api/term/run` SSE response was sent with `Connection: keep-alive`
but no Content-Length, so the browser's reader never saw end-of-stream
and the input stayed disabled after the first command finished. Now the
endpoint sends `Connection: close` and the client also breaks the read
loop on the `exit` event, so back-to-back commands work as expected.
