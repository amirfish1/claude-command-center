- Sidebar no longer flashes "No conversations in the last day" when a
  `/api/conversations/list` poll times out or is aborted. Failed fetches
  keep the last good snapshot instead of treating the miss as an empty
  archive.
