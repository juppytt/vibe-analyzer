You are given an API documentation file at {{INPUT_FILE}}.

Task:
- Using web access where needed, extract the API's input and output schema.
- Return a concise JSON schema summary for each endpoint.

Output format:
- JSON with an "endpoints" array
- Each endpoint has: method, path, input_schema, output_schema
