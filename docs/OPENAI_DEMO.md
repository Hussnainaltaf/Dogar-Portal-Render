# OpenAI API Demo

Dogar Portal.Com includes `scripts/openai_demo.py`, a small Python example built on the official OpenAI Responses API. It reads `OPENAI_API_KEY` from the environment, never prints the secret, and defaults to the model named by `OPENAI_MODEL` or `gpt-5`.

## Run it

From the repository root:

```bash
.venv/bin/python scripts/openai_demo.py --mode text
.venv/bin/python scripts/openai_demo.py --mode structured
.venv/bin/python scripts/openai_demo.py --mode tools
```

To run all three examples, use `--mode all`. Set the key in the shell or in a local, ignored `.env` file; do not commit it:

```bash
export OPENAI_API_KEY='your-api-key'
export OPENAI_MODEL='gpt-5'
.venv/bin/python scripts/openai_demo.py --mode text
```

The examples are intentionally small but demonstrate three application patterns. The **text** example makes a normal response request. The **structured** example asks for a candidate contact record that conforms to a strict JSON Schema, so application code receives predictable keys and types. The **tools** example defines an application-owned `lookup_office` function, lets the model request it, executes the function locally, and sends the result back for a final response.

> Keep tool execution on the application side. The model proposes a tool call; your server decides whether the operation is permitted, executes it, validates the result, and returns only the required output.

## Capability brief

| Capability | What it enables | Demonstrated here |
|---|---|---|
| Responses API | Text and image inputs, text outputs, stateful interactions, built-in tools, and application function calling. [1] | Text response |
| Structured Outputs | Responses that adhere to a supplied JSON Schema instead of merely producing valid JSON. [2] | Candidate extraction |
| Function calling | A controlled loop where the model requests an application function, your code executes it, and the result is returned to the model. [3] | Office lookup |
| Vision | Image understanding and image-generation workflows through supported models and endpoints. [4] | Not called by default to avoid requiring an image fixture |
| Audio and speech | Speech understanding and speech generation for voice-enabled workflows. [5] | Not called by default to avoid an audio fixture |
| Safety and refusals | Responses may explicitly indicate a refusal; structured-output consumers should handle that path before assuming the schema is present. [2] | The demo leaves production refusal handling to the caller |

For Dogar Portal.Com, suitable future integrations include extracting structured candidate fields from uploaded documents, summarizing demand-file notes, answering tenant-scoped operational questions, and using function calling for permission-checked lookups. Candidate and tenant records must remain behind the portal's existing authorization and tenant database boundary; the model must not be treated as an authorization layer.

## References

[1]: https://developers.openai.com/api/reference/responses/overview/ "OpenAI Responses API overview"

[2]: https://developers.openai.com/api/docs/guides/structured-outputs "OpenAI Structured Outputs guide"

[3]: https://developers.openai.com/api/docs/guides/function-calling "OpenAI Function Calling guide"

[4]: https://developers.openai.com/api/docs/guides/images-vision "OpenAI Images and Vision guide"

[5]: https://developers.openai.com/api/docs/guides/audio "OpenAI Audio and Speech guide"
