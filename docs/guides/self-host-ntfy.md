# Self-host coding agent notifications with ntfy

Run the local protocol example to try self-hosted ntfy without any external message service:

```console checked slow
$ python3 scripts/oncall-proof.py --with-agent
ntfy send / subscribe / reply          PASS
```

![Checked terminal output](img/self-host-ntfy.png)

Terminal capture rendered from the checked output above, with lines wrapped.

## What you will see

The script starts pinned ntfy and mail containers on random loopback ports, configures On-call with that ntfy address, tests delivery, and publishes replies to the separate reply topic. It removes its containers and temporary state afterward.

## Limits

For your installation, set the ntfy server address in Settings > On-call and subscribe to the printed topic. The phone must reach that address; loopback in this proof is reachable only on the test machine. Keep topic names private. The proof does not set up remote networking, TLS termination or ntfy authentication.

[Guide setup and verification](README.md)
