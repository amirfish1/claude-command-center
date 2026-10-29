The queue list and attention feeds reuse their last encoded response between rebuilds, so the polled 20 MB queue board no longer re-serialises and re-compresses on every request.
