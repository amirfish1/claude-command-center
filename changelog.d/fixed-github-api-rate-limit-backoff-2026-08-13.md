Cache `/api/issues` responses and back off GitHub-backed queue polling when GitHub API rate limits are low or exceeded, instead of repeatedly calling `gh issue list` into the limit.
