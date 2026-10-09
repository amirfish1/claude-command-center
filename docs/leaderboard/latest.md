# Free-model leaderboard

Run 2026-10-09T00:44:58+00:00 · suite v1 · 15 tasks · cost $0.00

Every model here cost $0 to call. Pass rate counts tasks whose fixed checker passed after the model's last edit. Latency is the median time per API request. Tokens are totals across all tasks, as reported by the provider. Infra errors (rate limits, unreachable models) count as failures and are listed so they are not mistaken for model mistakes.

| # | Model | Backend | Pass rate | Passed | Median request | Median task | Input tokens | Output tokens | Infra errors |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `nvidia/nemotron-3-super-120b-a12b:free` | openrouter | 100% | 15/15 | 1.0s | 14.2s | 46,419 | 6,185 | 0 |
| 2 | `apodex/apodex-1.1-mini:free` | openrouter | 100% | 15/15 | 1.4s | 14.9s | 42,946 | 5,152 | 0 |
| 3 | `nvidia/nemotron-3-ultra-550b-a55b:free` | openrouter | 100% | 15/15 | 2.8s | 16.1s | 34,682 | 3,686 | 0 |
| 4 | `cohere/north-mini-code:free` | openrouter | 100% | 15/15 | 5.5s | 30.4s | 24,987 | 5,910 | 0 |
| 5 | `dots-studio/dots-3-note-preview:free` | openrouter | 93% | 14/15 | 2.0s | 14.2s | 30,873 | 5,678 | 0 |
| 6 | `poolside/laguna-s-2.1:free` | openrouter | 93% | 14/15 | 3.0s | 46.2s | 24,473 | 3,808 | 1 |
| 7 | `nvidia/nemotron-3.5-lightning:free` | openrouter | 87% | 13/15 | 3.2s | 18.5s | 36,635 | 7,047 | 0 |
| 8 | `liquid/lfm-2.5-2.6b:free` | openrouter | 60% | 9/15 | 1.3s | 45.0s | 41,713 | 17,777 | 0 |
| 9 | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | openrouter | 40% | 6/15 | 2.8s | 15.9s | 16,767 | 11,356 | 0 |

## Not measured

Every request to these models failed, so they have no score:

- `google/gemma-4-26b-a4b-it:free` (openrouter): rate limited: Provider returned error
- `google/gemma-4-31b-it:free` (openrouter): rate limited: Provider returned error
- `poolside/laguna-xs-2.1:free` (openrouter): rate limited: Provider returned error
- `thinkingmachines/inkling-small:free` (openrouter): thinkingmachines/inkling-small:free is only available on agentic harnesses. Try plugging it into a coding agent or productivity app listed on https://openrouter
- `thinkingmachines/inkling:free` (openrouter): thinkingmachines/inkling:free is only available on agentic harnesses. Try plugging it into a coding agent or productivity app listed on https://openrouter.ai/ap

## Tasks

- `edit_file`: Fix a typo in a file
- `fix_test`: Make a failing test pass
- `add_function`: Add a new function
- `tool_read_write`: Read one file, write another
- `multi_step`: Read two files, combine, write
- `fizzbuzz`: Implement a function from a spec
- `off_by_one`: Fix an off-by-one bug
- `rename_symbol`: Rename a function across files
- `json_edit`: Edit a JSON config
- `count_errors`: Count matching lines in a log
- `palindrome`: Write code to pass given tests
- `handle_bad_input`: Handle bad input without crashing
- `new_module`: Create a module from a spec
- `csv_total`: Aggregate a CSV column
- `dedupe_sort`: Deduplicate and sort a list

## Skipped

- router: CCC free router not installed on the runner host
- github: not run; models.github.ai answered this runner host with a plain-text 200 instead of the inference API
