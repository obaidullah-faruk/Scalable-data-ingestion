import signal
from threading import Event


shutdown = Event()
signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
signal.signal(signal.SIGINT, lambda *_: shutdown.set())
shutdown.wait()
