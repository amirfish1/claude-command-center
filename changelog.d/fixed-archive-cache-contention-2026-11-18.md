Full archive list builders now read row templates in one batch, reducing cache-lock contention between concurrent readers while preserving current row data and cache limits.
