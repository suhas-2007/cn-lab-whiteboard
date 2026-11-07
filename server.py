"""TCP server for whiteboard rooms and saved boards."""
import argparse
import copy
import hashlib
import hmac
import json
import logging
from pathlib import Path
import queue
import re
import secrets
import socket
import threading
import time
from protocol import pack, receive

LOG = logging.getLogger("whiteboard")


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100000).hex()


class Peer:
    def __init__(self, sock):
        self.sock = sock
        self.out = queue.Queue(256)
        self.uid = None
        self.room = None
        self.closed = False
        threading.Thread(target=self.writer, daemon=True).start()

    def send(self, message):
        try:
            self.out.put_nowait(pack(message))
        except (queue.Full, ValueError):
            self.close()

    def writer(self):
        try:
            while not self.closed:
                data = self.out.get()
                if data is None:
                    break
                self.sock.sendall(data)
        except OSError:
            self.close()

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        try:
            self.out.put_nowait(None)
        except queue.Full:
            pass


class Server:
    def __init__(self, host="0.0.0.0", port=5050, storage="sessions.json"):
        self.lock = threading.RLock()
        self.storage = Path(storage)
        self.rooms = {}
        self.peers = set()
        self.running = True
        if self.storage.exists():
            self.rooms = json.loads(self.storage.read_text(encoding="utf-8"))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(32)
        self.sock.settimeout(1)
        self.port = self.sock.getsockname()[1]

    def save(self):
        temp = self.storage.with_suffix(".tmp")
        temp.write_text(json.dumps(self.rooms, allow_nan=False), encoding="utf-8")
        temp.replace(self.storage)

    def members(self, code):
        return [{"id": p.uid, "name": self.rooms[code]["users"][p.uid]["name"],
                 "role": "host" if p.uid == self.rooms[code]["owner"] else "editor",
                 "color": self.rooms[code]["users"][p.uid]["color"]}
                for p in self.peers if p.room == code and not p.closed]

    def broadcast(self, code, message):
        for peer in list(self.peers):
            if peer.room == code and not peer.closed:
                peer.send(message)

    def presence(self, code, notice):
        self.broadcast(code, {"type": "presence", "members": self.members(code), "notice": notice})

    def snapshot(self, peer):
        room = self.rooms[peer.room]
        peer.send({"type": "state", "seq": room["seq"], "ops": room["ops"],
                   "chat": room["chat"], "members": self.members(peer.room)})

    def join(self, peer, msg):
        code = str(msg.get("room", "")).strip().upper()
        name = str(msg.get("name", "")).strip()
        password = str(msg.get("password", ""))
        if not re.fullmatch(r"[A-Z0-9_-]{3,24}", code) or not 1 <= len(name) <= 32 or len(password) > 128:
            raise ValueError("Room: 3-24 letters/numbers. Name: 1-32 characters.")
        if code not in self.rooms:
            if not msg.get("create"):
                raise ValueError("Room does not exist. Select Create room to host it.")
            salt = secrets.token_hex(16)
            self.rooms[code] = dict(salt=salt, password=password_hash(password, salt), owner=None,
                                    seq=0, ops=[], chat=[], users={}, seen={})
        elif msg.get("create"):
            raise ValueError("Room already exists. Join it instead.")
        room = self.rooms[code]
        if not hmac.compare_digest(room["password"], password_hash(password, room["salt"])):
            raise ValueError("Wrong room password")
        token = str(msg.get("token", ""))
        uid = next((uid for uid, user in room["users"].items()
                    if token and hmac.compare_digest(user["token"], token)), None)
        if uid is None:
            if len(room["users"]) >= 256:
                raise ValueError("Room identity limit reached")
            uid = secrets.token_hex(8)
            token = secrets.token_hex(32)
            colors = ["#2563eb", "#dc2626", "#15803d", "#9333ea", "#b45309"]
            room["users"][uid] = dict(name=name, token=token, color=colors[len(room["users"]) % 5])
            room["seen"][uid] = []
            if room["owner"] is None:
                room["owner"] = uid
        for old in list(self.peers):
            if old is not peer and old.room == code and old.uid == uid:
                old.close()
                self.peers.discard(old)
        peer.uid, peer.room = uid, code
        self.save()
        peer.send({"type": "welcome", "id": uid, "token": token, "role": "host" if uid == room["owner"] else "editor"})
        self.snapshot(peer)
        self.presence(code, room["users"][uid]["name"] + " joined")

    def operation(self, peer, msg):
        room = self.rooms[peer.room]
        kind = msg.get("type")
        if kind == "ping":
            peer.send({"type": "pong"})
            return
        if kind == "sync":
            self.snapshot(peer)
            return
        if kind == "cursor":
            x, y = self.point(msg.get("point"))
            self.broadcast(peer.room, {"type": "cursor", "id": peer.uid, "point": [x, y]})
            return
        request = msg.get("request")
        if not isinstance(request, str) or not 1 <= len(request) <= 80:
            raise ValueError("Missing request ID")
        if request in room["seen"][peer.uid]:
            peer.send({"type": "ack", "request": request})
            return
        event = {"type": "event", "kind": kind, "user": peer.uid, "request": request}
        if kind == "draw":
            raw = msg.get("op", {})
            if not isinstance(raw, dict):
                raise ValueError("Invalid drawing")
            tool = raw.get("tool")
            points = raw.get("points")
            color, width = raw.get("color"), raw.get("width")
            if tool not in ("pen", "eraser", "line", "rectangle", "oval", "text"):
                raise ValueError("Invalid tool")
            if not isinstance(points, list) or not 1 <= len(points) <= 128:
                raise ValueError("Invalid points")
            points = [self.point(p) for p in points]
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Invalid color")
            if type(width) is not int or not 1 <= width <= 40:
                raise ValueError("Invalid width")
            text = str(raw.get("text", ""))[:300]
            if len(room["ops"]) >= 1000:
                raise ValueError("Board limit reached; save and clear the board")
            op = dict(id=request, user=peer.uid, tool=tool, points=points, color=color,
                      width=width, text=text, active=True, group=str(raw.get("group", request))[:80])
            # A new drawing invalidates this user's redo branch only.
            for old in room["ops"]:
                if old["user"] == peer.uid and not old["active"]:
                    old["redo"] = False
            room["ops"].append(op)
            event["op"] = op
        elif kind in ("undo", "redo"):
            candidates = [op for op in room["ops"] if op["user"] == peer.uid and
                          (op["active"] if kind == "undo" else not op["active"] and op.get("redo", True))]
            if candidates:
                target = candidates[-1] if kind == "undo" else min(candidates, key=lambda op: op.get("undone", 0))
                ids = []
                for op in room["ops"]:
                    if op["user"] == peer.uid and op["group"] == target["group"]:
                        op["active"] = kind == "redo"
                        op["undone"] = -(room["seq"] + 1)
                        ids.append(op["id"])
                event.update(ids=ids, active=kind == "redo")
            else:
                event.update(ids=[], active=kind == "redo")
        elif kind == "chat":
            text = str(msg.get("text", "")).strip()
            if not 1 <= len(text) <= 1000:
                raise ValueError("Chat must contain 1-1000 characters")
            chat = dict(name=room["users"][peer.uid]["name"], text=text,
                        time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            room["chat"].append(chat)
            room["chat"] = room["chat"][-500:]
            event["message"] = chat
        elif kind in ("clear", "save", "restore"):
            if peer.uid != room["owner"]:
                raise ValueError("Only the host may clear, save or restore checkpoints")
            if kind == "save":
                room["checkpoint"] = copy.deepcopy(room["ops"])
            elif kind == "clear":
                room["ops"] = []
            else:
                if "checkpoint" not in room:
                    raise ValueError("Save a checkpoint first")
                room["ops"] = copy.deepcopy(room["checkpoint"])
            event["ops"] = room["ops"]
        else:
            raise ValueError("Unknown operation")
        room["seq"] += 1
        event["seq"] = room["seq"]
        room["seen"][peer.uid].append(request)
        room["seen"][peer.uid] = room["seen"][peer.uid][-20000:]
        self.save()
        self.broadcast(peer.room, event)

    @staticmethod
    def point(value):
        if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(v) is not int or not 0 <= v <= 10000 for v in value):
            raise ValueError("Coordinates must be integers from 0 to 10000")
        return list(value)

    def handle(self, sock):
        sock.settimeout(20)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = Peer(sock)
        with self.lock:
            self.peers.add(peer)
        try:
            message = receive(sock)
            with self.lock:
                if message.get("type") != "join":
                    raise ValueError("Join first")
                self.join(peer, message)
            while self.running and not peer.closed:
                message = receive(sock)
                with self.lock:
                    try:
                        self.operation(peer, message)
                    except (ValueError, TypeError, KeyError) as error:
                        peer.send({"type": "error", "message": str(error), "request": message.get("request")})
        except (OSError, EOFError, ValueError, TypeError, KeyError) as error:
            LOG.info("Connection ended: %s", error)
            if not peer.room:
                try:
                    sock.sendall(pack({"type": "fatal", "message": str(error)}))
                except OSError:
                    pass
        finally:
            with self.lock:
                self.peers.discard(peer)
                peer.close()
                if peer.room:
                    self.presence(peer.room, self.rooms[peer.room]["users"][peer.uid]["name"] + " left")

    def run(self):
        print(f"Whiteboard server listening on port {self.port}. Ctrl+C to stop.", flush=True)
        while self.running:
            try:
                sock, _ = self.sock.accept()
                with self.lock:
                    if len(self.peers) >= 64:
                        sock.close()
                        continue
                threading.Thread(target=self.handle, args=(sock,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self.running = False
        self.sock.close()
        with self.lock:
            for peer in list(self.peers):
                peer.close()
            self.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--storage", default="sessions.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    app = Server(args.host, args.port, args.storage)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
