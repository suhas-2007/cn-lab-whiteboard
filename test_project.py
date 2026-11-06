"""Protocol and live-socket tests. Run: python -m unittest -v"""
import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import uuid
from protocol import pack, receive
from server import Server


class WireClient:
    def __init__(self, port, name, create=False, token="", password="lab", room="TEST"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        self.send(dict(type="join", room=room, name=name, create=create, token=token, password=password))
        self.welcome = self.until("welcome")
        self.state = self.until("state")

    def send(self, msg):
        self.sock.sendall(pack(msg))

    def until(self, kind, request=None):
        while True:
            msg = receive(self.sock)
            if msg["type"] == "fatal":
                self.sock.close()
                raise ValueError(msg["message"])
            if msg["type"] == kind and (request is None or msg.get("request") == request):
                return msg

    def operation(self, kind, **kwargs):
        msg = dict(type=kind, request=uuid.uuid4().hex, **kwargs)
        self.send(msg)
        return self.until("event", msg["request"])

    def close(self):
        self.sock.close()


def drawing(group="stroke", color="#123456"):
    return dict(tool="pen", points=[[20, 30], [200, 300]], color=color, width=3, group=group)


class ProtocolTests(unittest.TestCase):
    def test_fragmentation_and_coalescing(self):
        left,right = socket.socketpair()
        try:
            first,second={"type":"chat", "text":"hello\nworld"},{"type":"ping"}
            data=pack(first)+pack(second)
            def writer():
                for value in data:
                    left.sendall(bytes([value]))
            thread=threading.Thread(target=writer); thread.start()
            self.assertEqual(receive(right),first)
            self.assertEqual(receive(right),second)
            thread.join()
        finally:
            left.close(); right.close()

    def test_invalid_payload_and_truncated_frame(self):
        import struct
        for payload in (b"not-json", b"[]", b"null"):
            left, right = socket.socketpair()
            try:
                left.sendall(struct.pack("!I", len(payload)) + payload)
                with self.assertRaises(ValueError):
                    receive(right)
            finally:
                left.close(); right.close()
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack("!I", 20) + b"short")
            left.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                receive(right)
        finally:
            left.close(); right.close()

    def test_utf8_and_exact_frame_layout(self):
        import struct
        message = {"type": "chat", "text": "Hello, नमस्ते"}
        data = pack(message)
        self.assertEqual(struct.unpack("!I", data[:4])[0], len(data) - 4)
        self.assertEqual(json.loads(data[4:]), message)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"sessions.json"
        self.server=Server("127.0.0.1",0,self.path)
        self.thread=threading.Thread(target=self.server.run,daemon=True); self.thread.start()
        self.clients=[]

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop(); self.thread.join(3)
        time.sleep(.05)
        self.temp.cleanup()

    def client(self,name,**kwargs):
        client=WireClient(self.server.port,name,**kwargs); self.clients.append(client)
        return client

    def test_join_drawing_chat_permissions_and_checkpoint(self):
        host=self.client("Host",create=True)
        editor=self.client("Editor")
        event=host.operation("draw",op=drawing())
        self.assertEqual(editor.until("event",event["request"])["op"],event["op"])
        saved=host.operation("save")
        host.operation("clear")
        restored=host.operation("restore")
        self.assertEqual(restored["ops"],saved["ops"])
        chat=editor.operation("chat",text="Ready for the lab")
        self.assertEqual(chat["message"]["name"],"Editor")
        request=uuid.uuid4().hex
        editor.send(dict(type="clear",request=request))
        self.assertIn("host",editor.until("error",request)["message"])
        new=self.client("Late join")
        self.assertEqual(len(new.state["ops"]),1)
        self.assertEqual(len(new.state["chat"]),1)
        self.assertEqual(len(new.state["members"]),3)

    def test_duplicate_invalid_input_and_own_undo(self):
        host=self.client("Host",create=True); editor=self.client("Editor")
        first=host.operation("draw",op=drawing("A"))
        host.operation("draw",op=drawing("A"))
        editor.operation("draw",op=drawing("B"))
        undone=host.operation("undo")
        self.assertEqual(len(undone["ids"]),2)
        with self.server.lock:
            ops=copy.deepcopy(self.server.rooms["TEST"]["ops"])
        self.assertEqual([op["active"] for op in ops],[False,False,True])
        host.operation("redo")
        host.send(dict(type="draw",request=first["request"],op=drawing("A")))
        host.until("ack",first["request"])
        bad=uuid.uuid4().hex
        host.send(dict(type="draw",request=bad,op=dict(drawing(),points=[[-1,0]])))
        host.until("error",bad)
        with self.server.lock:
            self.assertEqual(len(self.server.rooms["TEST"]["ops"]),3)

    def test_simultaneous_writers_have_identical_order(self):
        clients=[self.client("Host",create=True)]+[self.client(f"User{i}") for i in range(3)]
        requests=[]
        def write(client,index):
            for n in range(10):
                request=f"{index}-{n}"
                client.send(dict(type="draw",request=request,op=drawing(request)))
        threads=[threading.Thread(target=write,args=(c,i)) for i,c in enumerate(clients)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        for client in clients:
            sequence=[client.until("event")["seq"] for _ in range(40)]
            self.assertEqual(sequence,list(range(1,41)))

    def test_reconnect_identity_and_restart_recovery(self):
        host=self.client("Host",create=True)
        token=host.welcome["token"]
        host.operation("draw",op=drawing())
        host.close()
        restored=self.client("Host",token=token)
        self.assertEqual(restored.welcome["role"],"host")
        self.assertEqual(len(restored.state["ops"]),1)
        self.server.stop(); self.thread.join(3)
        self.server=Server("127.0.0.1",0,self.path)
        self.thread=threading.Thread(target=self.server.run,daemon=True); self.thread.start()
        restarted=self.client("Host",token=token)
        self.assertEqual(restarted.welcome["id"],host.welcome["id"])
        self.assertEqual(len(restarted.state["ops"]),1)

    def test_wrong_password_and_oversized_frame(self):
        self.client("Host",create=True)
        with self.assertRaises(ValueError):
            WireClient(self.server.port,"Intruder",password="wrong")
        sock=socket.create_connection(("127.0.0.1",self.server.port),timeout=5)
        sock.sendall(b"\xff\xff\xff\xff")
        self.assertEqual(receive(sock)["type"],"fatal")
        sock.close()

    def test_room_isolation_and_membership_changes(self):
        host = self.client("Host", create=True)
        other = self.client("Other host", create=True, room="OTHER")
        host.until("presence")
        visitor = self.client("Visitor")
        presence = host.until("presence")
        self.assertEqual(len(presence["members"]), 2)
        host.operation("draw", op=drawing())
        other.send({"type": "sync"})
        state = other.until("state")
        self.assertEqual(state["ops"], [])
        self.assertEqual(len(state["members"]), 1)
        visitor.close()
        self.assertEqual(len(host.until("presence")["members"]), 1)

    def test_all_tools_and_redo_branch(self):
        host = self.client("Host", create=True)
        for tool in ("pen", "eraser", "line", "rectangle", "oval", "text"):
            event = host.operation("draw", op=dict(drawing(tool), tool=tool, text="Annotation"))
            self.assertEqual(event["op"]["tool"], tool)
        host.operation("undo")
        host.operation("draw", op=drawing("new-stroke"))
        self.assertEqual(host.operation("redo")["ids"], [])
        host.send({"type": "sync"})
        state = host.until("state")
        self.assertEqual(sum(op["active"] for op in state["ops"]), 6)
        self.assertFalse(next(op for op in state["ops"] if op["tool"] == "text")["active"])


if __name__ == "__main__":
    unittest.main()
