Keep a small reserve of connection workers ready so busy dashboard traffic does not make the accept loop wait for a new thread, while preserving concurrent streams and graceful shutdown.
