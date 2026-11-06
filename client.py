"""Desktop whiteboard client. Tk owns the UI; a worker owns the socket."""
import json
from pathlib import Path
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox, simpledialog
import uuid
from protocol import pack, receive


class Client:
    def __init__(self, root):
        self.root = root
        root.title("LAN Whiteboard | CN Lab")
        root.geometry("1240x800")
        root.minsize(900, 600)
        self.inbox, self.outbox = queue.Queue(), queue.Queue(512)
        self.stop_event = threading.Event()
        self.connected = False
        self.worker = None
        self.ops, self.members, self.chats = [], [], []
        self.pending = {}
        self.seq, self.uid = 0, None
        self.color = "#1e3a5f"
        self.tool, self.width = tk.StringVar(value="pen"), tk.IntVar(value=3)
        self.status = tk.StringVar(value="Enter the server LAN address and room details.")
        self.tokens_path = Path.home() / ".cn_whiteboard_tokens.json"
        try:
            self.tokens = json.loads(self.tokens_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.tokens = {}
        self.build()
        self.points, self.group = [], None
        self.cursor_time = 0
        root.after(30, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(8, 5))
        style.configure("TLabel", font=("Segoe UI", 10))
        bar = ttk.Frame(self.root, padding=10)
        bar.pack(fill="x")
        self.fields = {}
        for label, value, size in [("Server", "127.0.0.1", 16), ("Port", "5050", 6),
                                    ("Room", "CN-LAB", 12), ("Name", "", 13), ("Password", "", 12)]:
            ttk.Label(bar, text=label).pack(side="left", padx=(0, 4))
            entry = ttk.Entry(bar, width=size, show="*" if label == "Password" else "")
            entry.insert(0, value)
            entry.pack(side="left", padx=(0, 10))
            self.fields[label] = entry
        self.create = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Create room", variable=self.create).pack(side="left")
        self.connect_button = ttk.Button(bar, text="Connect", command=self.connect)
        self.connect_button.pack(side="left", padx=5)
        ttk.Button(bar, text="Leave", command=self.leave).pack(side="left")
        tools = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        tools.pack(fill="x")
        for tool in ["pen", "eraser", "line", "rectangle", "oval", "text"]:
            ttk.Radiobutton(tools, text=tool.title(), value=tool, variable=self.tool).pack(side="left", padx=3)
        ttk.Button(tools, text="Color", command=self.choose_color).pack(side="left", padx=5)
        ttk.Spinbox(tools, from_=1, to=40, width=3, textvariable=self.width).pack(side="left")
        for label, kind in [("Undo", "undo"), ("Redo", "redo"), ("Clear", "clear"),
                            ("Save checkpoint", "save"), ("Restore", "restore")]:
            ttk.Button(tools, text=label, command=lambda k=kind: self.control(k)).pack(side="left", padx=2)
        ttk.Button(tools, text="Export SVG", command=self.export).pack(side="left", padx=2)
        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=10)
        self.canvas = tk.Canvas(content, background="white", highlightthickness=1, highlightbackground="#ccd2d8")
        content.add(self.canvas, weight=4)
        side = ttk.Frame(content, width=260, padding=(10, 0))
        content.add(side, weight=1)
        ttk.Label(side, text="Participants", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.people = tk.Listbox(side, height=6, relief="flat", font=("Segoe UI", 10))
        self.people.pack(fill="x", pady=5)
        ttk.Label(side, text="Group chat", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(10, 4))
        self.chat = tk.Text(side, width=29, state="disabled", wrap="word", relief="flat", font=("Segoe UI", 10))
        self.chat.pack(fill="both", expand=True)
        self.chat_entry = ttk.Entry(side)
        self.chat_entry.pack(fill="x", pady=5)
        self.chat_entry.bind("<Return>", lambda _: self.send_chat())
        ttk.Button(side, text="Send", command=self.send_chat).pack(anchor="e")
        ttk.Label(self.root, textvariable=self.status, padding=8).pack(fill="x")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Motion>", self.cursor)
        self.canvas.bind("<Configure>", lambda _: self.redraw())

    def connect(self):
        if self.worker and self.worker.is_alive():
            self.status.set("Already connected or reconnecting. Use Leave first.")
            return
        values = {key: entry.get().strip() for key, entry in self.fields.items()}
        try:
            port = int(values["Port"])
            if not 1 <= port <= 65535 or not values["Name"]:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Connection", "Enter a name and port between 1 and 65535.")
            return
        self.key = f'{values["Server"]}:{port}/{values["Room"].upper()}'
        self.join = dict(type="join", name=values["Name"], room=values["Room"], password=values["Password"],
                         create=self.create.get(), token=self.tokens.get(self.key, ""))
        self.pending.clear()
        self.stop_event = threading.Event()
        self.outbox = queue.Queue(512)
        self.worker = threading.Thread(target=self.network, args=(values["Server"], port), daemon=True)
        self.worker.start()
        self.connect_button.configure(state="disabled")

    def network(self, host, port):
        while not self.stop_event.is_set():
            sock = None
            try:
                self.inbox.put({"type": "status", "message": "Connecting..."})
                sock = socket.create_connection((host, port), timeout=5)
                self.socket = sock
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(pack(self.join))
                sock.settimeout(1)
                # A reader preserves partial frames across short idle periods.
                sock.settimeout(20)
                broken = threading.Event()
                def reader(connection=sock):
                    try:
                        while not self.stop_event.is_set():
                            message = receive(connection)
                            self.inbox.put(message)
                            if message.get("type") == "fatal":
                                self.stop_event.set()
                                break
                    except (OSError, EOFError, ValueError):
                        pass
                    finally:
                        broken.set()
                threading.Thread(target=reader, daemon=True).start()
                last_ping = 0
                while not self.stop_event.is_set() and not broken.is_set():
                    try:
                        message = self.outbox.get(timeout=.2)
                        sock.sendall(pack(message))
                    except queue.Empty:
                        pass
                    if time.monotonic() - last_ping > 5:
                        sock.sendall(pack({"type": "ping"}))
                        last_ping = time.monotonic()
            except (OSError, ValueError) as error:
                self.inbox.put({"type": "status", "message": str(error)})
            finally:
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
                self.inbox.put({"type": "offline"})
            if self.stop_event.wait(2):
                break

    def send(self, msg):
        try:
            self.outbox.put_nowait(msg)
        except queue.Full:
            self.status.set("Network queue full. Reconnect to recover the latest state.")

    def submit(self, kind, **fields):
        if not self.connected:
            self.status.set("Connect before editing. Waiting for the latest board state.")
            return
        msg = dict(type=kind, request=uuid.uuid4().hex, **fields)
        self.pending[msg["request"]] = msg
        self.send(msg)

    def poll(self):
        for _ in range(500):
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                break
            kind = msg.get("type")
            if kind == "welcome":
                self.uid = msg["id"]
                self.join.update(token=msg["token"], create=False)
                self.tokens[self.key] = msg["token"]
                try:
                    self.tokens_path.write_text(json.dumps(self.tokens), encoding="utf-8")
                except OSError:
                    self.status.set("Identity could not be saved; keep this client open for host recovery.")
            elif kind == "state":
                self.ops, self.chats, self.seq = msg["ops"], msg["chat"], msg["seq"]
                self.connected = True
                self.update_people(msg["members"])
                self.redraw()
                self.show_chat()
                self.status.set(f'Connected | Room {self.join["room"].upper()} | revision {self.seq}')
                for pending in list(self.pending.values()):
                    self.send(pending)
            elif kind == "event":
                self.pending.pop(msg.get("request"), None)
                if msg["seq"] <= self.seq:
                    continue
                if msg["seq"] != self.seq + 1:
                    self.connected = False
                    self.send({"type": "sync"})
                    continue
                self.seq = msg["seq"]
                event = msg["kind"]
                if event == "draw":
                    self.ops.append(msg["op"])
                elif event in ("undo", "redo"):
                    ids = set(msg["ids"])
                    for op in self.ops:
                        if op["id"] in ids:
                            op["active"] = msg["active"]
                elif event == "chat":
                    self.chats.append(msg["message"])
                    self.show_chat()
                elif "ops" in msg:
                    self.ops = msg["ops"]
                self.redraw()
                self.status.set(f'Room {self.join["room"].upper()} | revision {self.seq} | {event}')
            elif kind == "ack":
                self.pending.pop(msg.get("request"), None)
            elif kind == "presence":
                self.update_people(msg["members"])
                self.status.set(msg["notice"])
            elif kind == "cursor" and msg["id"] != self.uid:
                self.draw_cursor(msg)
            elif kind in ("error", "fatal", "status"):
                self.pending.pop(msg.get("request"), None)
                self.status.set(msg["message"])
                if kind == "fatal":
                    messagebox.showerror("Connection", msg["message"])
            elif kind == "offline":
                self.connected = False
                if self.stop_event.is_set():
                    self.connect_button.configure(state="normal")
                else:
                    self.status.set("Disconnected. Reconnecting in 2 seconds; edits paused.")
        self.root.after(30, self.poll)

    def update_people(self, members):
        self.members = members
        self.people.delete(0, "end")
        self.canvas.delete("cursor")
        for i, member in enumerate(members):
            self.people.insert("end", f'{member["name"]} ({member["role"]})')
            self.people.itemconfigure(i, foreground=member["color"])

    def show_chat(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        for msg in self.chats[-500:]:
            self.chat.insert("end", f'{msg["time"][11:19]} UTC  {msg["name"]}\n{msg["text"]}\n\n')
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def send_chat(self):
        text = self.chat_entry.get().strip()
        if text and self.connected:
            self.submit("chat", text=text)
            self.chat_entry.delete(0, "end")

    def choose_color(self):
        value = colorchooser.askcolor(self.color, parent=self.root)[1]
        if value:
            self.color = value

    def position(self, event):
        return [max(0, min(10000, round(event.x * 10000 / max(1, self.canvas.winfo_width())))),
                max(0, min(10000, round(event.y * 10000 / max(1, self.canvas.winfo_height()))))]

    def screen(self, point):
        return point[0] * self.canvas.winfo_width() / 10000, point[1] * self.canvas.winfo_height() / 10000

    def press(self, event):
        if not self.connected:
            return
        self.group = uuid.uuid4().hex
        self.points = [self.position(event)]
        if self.tool.get() == "text":
            text = simpledialog.askstring("Annotation", "Text:", parent=self.root)
            if text:
                self.draw_submit(self.points, text)
            self.points = []

    def drag(self, event):
        self.cursor(event)
        if not self.points or not self.connected:
            return
        point = self.position(event)
        if self.tool.get() in ("pen", "eraser"):
            self.points.append(point)
            if len(self.points) >= 8:
                self.draw_submit(self.points)
                self.points = [point]
        else:
            self.points = [self.points[0], point]
        self.canvas.delete("preview")
        self.paint(dict(tool=self.tool.get(), points=self.points, color=self.color, width=self.safe_width(), text=""), "preview")

    def release(self, event):
        if self.points and self.connected:
            self.points.append(self.position(event))
            self.draw_submit(self.points)
        self.points = []
        self.canvas.delete("preview")

    def safe_width(self):
        try:
            return max(1, min(40, self.width.get()))
        except tk.TclError:
            return 3

    def draw_submit(self, points, text=""):
        self.submit("draw", op=dict(tool=self.tool.get(), points=points, color=self.color,
                                    width=self.safe_width(), group=self.group, text=text))

    def paint(self, op, tag="board"):
        points = [self.screen(point) for point in op["points"]]
        coords = [v for point in points for v in point]
        color = "white" if op["tool"] == "eraser" else op["color"]
        width = op["width"]
        if op["tool"] in ("pen", "eraser", "line"):
            if len(points) == 1 or all(p == points[0] for p in points):
                x, y = points[0]
                self.canvas.create_oval(x-width/2, y-width/2, x+width/2, y+width/2, fill=color, outline=color, tags=tag)
            else:
                self.canvas.create_line(*coords, fill=color, width=width, capstyle="round", joinstyle="round", tags=tag)
        elif op["tool"] == "text":
            self.canvas.create_text(*points[0], text=op["text"], fill=color, anchor="nw", font=("Segoe UI", 12+width), tags=tag)
        elif len(points) > 1:
            x1, y1 = points[0]
            x2, y2 = points[-1]
            method = self.canvas.create_rectangle if op["tool"] == "rectangle" else self.canvas.create_oval
            method(min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2), outline=color, width=width, tags=tag)

    def redraw(self):
        self.canvas.delete("board")
        for op in self.ops:
            if op["active"]:
                self.paint(op)
        self.canvas.tag_raise("cursor")

    def cursor(self, event):
        if self.connected and time.monotonic() - self.cursor_time > .1:
            self.send({"type": "cursor", "point": self.position(event)})
            self.cursor_time = time.monotonic()

    def draw_cursor(self, msg):
        member = next((m for m in self.members if m["id"] == msg["id"]), None)
        if member:
            tag = "cursor_" + msg["id"]
            self.canvas.delete(tag)
            x,y = self.screen(msg["point"])
            self.canvas.create_text(x,y,text="+ " + member["name"], fill=member["color"], anchor="nw", tags=("cursor",tag))

    def control(self, kind):
        if kind in ("clear", "restore") and not messagebox.askyesno(kind.title(), "Change the shared board for everyone?"):
            return
        self.submit(kind)

    def export(self):
        from html import escape
        path = filedialog.asksaveasfilename(defaultextension=".svg", filetypes=[("SVG image", "*.svg")])
        if not path:
            return
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 10000 10000">', '<rect width="10000" height="10000" fill="white"/>']
        for op in self.ops:
            if not op["active"]:
                continue
            color = "white" if op["tool"] == "eraser" else op["color"]
            width = op["width"] * 10000 / max(1, self.canvas.winfo_width())
            points = op["points"]
            x,y = points[0]
            style = f'fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'
            if op["tool"] == "text":
                parts.append(f'<text x="{x}" y="{y}" dominant-baseline="hanging" font-size="{(12+op["width"])*16}" fill="{color}">{escape(op["text"])}</text>')
            elif op["tool"] in ("rectangle", "oval") and len(points)>1:
                xx,yy=points[-1]; left,top=min(x,xx),min(y,yy); w,h=abs(xx-x),abs(yy-y)
                if op["tool"] == "rectangle":
                    parts.append(f'<rect x="{left}" y="{top}" width="{w}" height="{h}" {style}/>')
                else:
                    parts.append(f'<ellipse cx="{left+w/2}" cy="{top+h/2}" rx="{w/2}" ry="{h/2}" {style}/>')
            else:
                parts.append(f'<polyline points="{" ".join(f"{a},{b}" for a,b in points)}" {style}/>')
        parts.append("</svg>")
        Path(path).write_text("\n".join(parts), encoding="utf-8")
        self.status.set("Canvas exported to " + path)

    def leave(self):
        self.stop_event.set()
        self.connected = False
        self.points = []
        if hasattr(self,"socket"):
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.status.set("Left the session.")
        self.connect_button.configure(state="normal")

    def close(self):
        self.leave()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Client(root)
    root.mainloop()
