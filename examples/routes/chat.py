from autowire import websocket


@websocket("/chat", auth=False)
async def connect(socket):
    await socket.send("Welcome!")
    async for message in socket:
        await socket.send(f"Echo: {message}")
