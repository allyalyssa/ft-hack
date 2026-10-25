#!/usr/bin/env python3
"""Plain TCP relay so the OpenShell sandbox can reach host Ollama.
Binds only on the sandbox bridge (host.openshell.internal), never on Wi-Fi."""
import asyncio, sys
LISTEN_HOST = sys.argv[1] if len(sys.argv) > 1 else "172.18.0.1"
LISTEN_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 11436
TARGET = ("127.0.0.1", 11434)

async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def handle(client_r, client_w):
    try:
        up_r, up_w = await asyncio.open_connection(*TARGET)
    except Exception:
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))

async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    print(f"relay {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET[0]}:{TARGET[1]}", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
