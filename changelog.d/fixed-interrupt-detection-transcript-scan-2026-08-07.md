Fixed "[Request interrupted by user]" events going undetected when nobody was actively watching the session — interrupt detection now runs on transcript scan, not just the SSE stream poller.
