FROM python:3.12-slim

WORKDIR /app

# Install the package
COPY . .
RUN pip install --no-cache-dir .

# Default: stdio mode (MCP client connects via subprocess)
# For hosted mode, use the unified-researchdata-mcp server instead.
ENTRYPOINT ["datatagger-mcp"]
