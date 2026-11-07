# Demo and viva notes

## Demo order

1. Start the server and connect two clients to the same room.
2. Draw from both clients. Change colors, erase something and add text.
3. Send a chat message. Check the sender name and time on both windows.
4. Undo one client's stroke. The other client's work should stay.
5. Save the board as host, clear it, then load the saved board.
6. Try Clear from the other client. The server should reject it.
7. Join another client. It should receive the existing board and chat.
8. Disconnect one client, draw from the other, then reconnect it.
9. Restart the server with the same sessions.json file and check recovery.

Keep the local network on and internet off for the lab evaluation.

## Code to explain

| File | Points to cover |
|---|---|
| `server.py` | Rooms, the lock, outgoing queues, host checks and saving state |
| `protocol.py` | JSON messages, length header and the receive loop |
| `client.py` | Canvas events, the network thread, undo groups and reconnect |
| `test_project.py` | Concurrent edits, duplicate requests and restart recovery |

Be ready to explain why one recv() call may return only part of a message, why all
clients need the same edit order, and how the server knows which user can clear a
board. Also explain what happens if a connection drops just after sending an edit.

## Recording

Divide the explanation based on each member's contribution. Each member's name and
face must be visible in the
recording. Use the slides and source code. The assignment does not require a live
app demo in the video, but everyone must attend the in-person demo.

Put the recording in `release` and update the submission ZIP. One member submits it.
