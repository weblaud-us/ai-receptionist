# Sample call recordings

The demo section plays simulated calls with a timed transcript until real recordings exist.
To use a recording, add the file here and set `audio` on that script in `src/data/scenarios.ts`:

| Scenario | AU file | UK file |
|---|---|---|
| Implant price guardrail | `implant-price-au.mp3` | `implant-price-uk.mp3` |
| Health fund / NHS to private | `regional-au.mp3` | `regional-uk.mp3` |
| Red-flag emergency | `emergency-au.mp3` | `emergency-uk.mp3` |
| Missed-call text-back | `text-back-au.mp3` | `text-back-uk.mp3` |

Keep the transcript `at` times in step with the audio. Use recordings of the real product on a
demo practice: never a patient call.
