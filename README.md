# LAN Whiteboard - CN Lab

A desktop whiteboard for a group on the same LAN. One machine runs the server;
each participant runs a client. There are no web services, accounts or online assets.

## Run the Windows applications

1. Extract the submission ZIP. Keep it in a folder where you can write files.
2. On the host machine, run `release/WhiteboardServer.exe`. It listens on TCP port 5050.
3. Run `release/WhiteboardClient.exe` on that machine. Use server `127.0.0.1`,
   port `5050`, a room code, your name and a room password. Select **Create room**.
4. On other machines, run the client, enter the host's LAN IPv4 address and the same
   port, room and password. Leave **Create room** unchecked.
5. On Windows, allow the server through the firewall for the private LAN. Run
   `ipconfig` on the server to find the active adapter's IPv4 address. Do not use
   `127.0.0.1` on a different computer.

The first participant to create the room is its host. Only that identity can clear,
save or restore a checkpoint. Everyone can draw, annotate, chat, export and undo
their own work. The server can host several rooms; their boards and chats are separate.

To choose another port from PowerShell:

```powershell
.\release\WhiteboardServer.exe --port 6060 --storage .\sessions.json
```

Keep the server running during the session. Close its console with Ctrl+C when done.
The server saves accepted edits to `sessions.json` in its working directory. Restart
it using the same file to recover rooms, board state, chat and checkpoints.

## Board controls

- Pen and eraser send short stroke segments during dragging. A whole drag is one undo group.
- Line, rectangle and oval commit on mouse release. Text asks for an annotation.
- Undo affects your latest active stroke or annotation. Redo restores your latest
  undone group. Drawing again discards your redo branch. Other users' work stays in order.
- Clear starts a new undo history. Save checkpoint first if you want to restore the old board.
- Save checkpoint and Restore are shared server actions; they do not open a local file dialog.
- Export SVG saves the visible board as an image that opens in a browser without internet.
- A disconnected client pauses editing and retries every two seconds. After joining
  again, it takes a fresh snapshot and resends unacknowledged requests. Duplicate IDs
  prevent the same edit being applied twice.

The client stores a recovery token in `.cn_whiteboard_tokens.json` in the user's home
folder. Keep it to recover the same identity, including host permissions. Do not share
this file. A different computer or Windows account creates a different identity.

## Run from source

Python 3.10 or later with Tk is sufficient; there are no third-party runtime packages.

```powershell
python server.py
python client.py
python -m unittest -v
```

To rebuild the Windows executables, install PyInstaller in a development environment,
then run `./build.ps1`. This installation is only for building; the submitted EXEs
work without Python or internet. Build on Windows for Windows.

## Files

`server.py` coordinates rooms and persists state. `client.py` implements the GUI and
reconnect worker. `protocol.py` handles TCP message framing. `test_project.py` tests
real sockets and shared-board behavior. `release` contains the executables,
technical report and slides.

## Before submission

Read the technical report and practise the demonstration on at least two physical
LAN machines with internet disabled. Local socket tests cannot verify your router,
Wi-Fi isolation or firewall settings. A private LAN may work without internet;
disconnect the WAN connection, not the LAN adapter.

Record the explanation video with each member's actual name, face and contribution.
See `release/TEAM_RECORDING.md`. Add that recording and your team details to the ZIP.
The assignment's printed deadline is 15 November 2026, 11:55 pm IST.

## Practical limits

64 simultaneous connections per server, 256 stored identities per room, 1000 drawing
segments per board, 500 retained chat messages and an 8 MiB frame limit. Save a
checkpoint, export, then clear a full board. One checkpoint per room is retained.
Disk writes serialize edits and favour recoverability over large-scale throughput.
Host permissions remain attached to the host's recovery token when the host leaves.
Passwords and content travel on the private LAN without TLS; this is a trusted lab
network application, not a public internet service. There is no offline editing.
