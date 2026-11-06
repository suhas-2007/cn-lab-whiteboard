# Team explanation and final submission

The explanation video must be recorded by the actual team. Each person's name and
face must be visible. Explain the presentation and source; the assignment does not
require a live application demo inside this recording.

Before recording, add your team names and roll numbers to the presentation's first
slide and report cover. Assign contributions according to what each person actually
implemented, tested, reviewed or can explain. Do not claim work someone did not do.

Suggested speaking order (adapt to your group size):

1. Requirements and architecture: explain why the server owns the board, the LAN
   setup, room creation, host permissions and late joins. Open `server.py`.
2. Protocol and concurrency: explain the four-byte length header, UTF-8 JSON, TCP
   stream framing, the server lock, writer queues and sequence numbers. Open
   `protocol.py`, `Peer` and `Server.operation`.
3. Client and recovery: show the canvas tools, chat, normalized coordinates, Tk
   event queue, reconnect snapshot, request IDs and identity token. Open `client.py`.
4. Testing and demonstration: explain concurrent drawing, room isolation, invalid
   message handling, checkpoints and restart recovery. Open `test_project.py`.

Questions each member should be able to answer:

- Why doesn't one TCP `recv` equal one application message?
- What happens when two users draw at the same time?
- How can a client retry an operation without duplicating it?
- What happens to another person's work when I undo?
- How does a late participant receive the current board?
- How does the server prevent edits from reaching another room?

For the in-person evaluation, all members must attend. Extract the submitted ZIP,
start the server, connect at least two machines, draw concurrently, chat, undo/redo,
join a third client, test checkpoint restore, disconnect/reconnect a client and test
checkpoint recovery. Keep internet disabled while keeping the local network active.

Add the recorded video to `release` and make the final ZIP with the source files,
both EXEs, technical PDF and PPTX. Only one member needs to submit the archive.
