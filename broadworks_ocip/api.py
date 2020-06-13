import hashlib
import inspect
import logging
import socket
import sys
import uuid

from classforge import Class
from classforge import Field
from lxml import etree

import broadworks_ocip.requests
import broadworks_ocip.responses
import broadworks_ocip.types


FORMATTER = logging.Formatter(
    "%(asctime)s — %(name)s — %(levelname)s — %(funcName)s:%(lineno)d — %(message)s",
)


class BroadoworksAPI(Class):
    session = Field(type=str)
    host = Field(type=str, required=True)
    port = Field(type=int, default=2208)
    username = Field(type=str, required=True)
    password = Field(type=str, required=True)
    logger = Field(type=object)
    despatch_table = Field(type=dict)
    connected = Field(type=bool, default=False)
    timeout = Field(type=int, default=8)
    socket = Field(type=object, default=None)
    instream = Field(type=object, default=None)

    def on_init(self):
        if self.session is None:
            self.session = str(uuid.uuid4())
        if self.logger is None:
            self.configure_logger()
        self.build_despatch_table()
        self.connected = False

    def build_despatch_table(self):
        self.logger.debug("Building Broadworks despatch table")
        despatch_table = {}
        for module in (broadworks_ocip.responses, broadworks_ocip.requests):
            for name, data in inspect.getmembers(module, inspect.isclass):
                if name.startswith("__"):
                    continue
                try:
                    if data.__module__ in (
                        "broadworks_ocip.types",
                        "broadworks_ocip.requests",
                        "broadworks_ocip.responses",
                    ):
                        despatch_table[name] = data
                except AttributeError:
                    continue
        self.despatch_table = despatch_table
        self.logger.debug("Built Broadworks despatch table")

    def configure_logger(self):
        logger = logging.getLogger("broadworks_api")
        logger.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(FORMATTER)
        logger.addHandler(console_handler)
        self.logger = logger

    def get_command_xml(self, command, **kwargs):
        try:
            cls = self.despatch_table[command]
        except KeyError as e:
            self.logger.error(f"Unknown command requested - {command}")
            raise e
        cmd = cls(**kwargs)
        return cmd._build_xml(self.session)

    def send_command(self, command, **kwargs):
        xml = self.get_command_xml(command, **kwargs)
        self.logger.debug(f"SEND: {str(xml)}")
        self.socket.sendall(xml + b"\n")

    def receive_response(self):
        content = b""
        while True:
            line = self.instream.readline()
            content += line.encode()
            if line.endswith("</BroadsoftDocument>\n"):
                break
        self.logger.debug(f"RECV: {str(content)}")
        return self.decode_xml(content)

    def decode_xml(self, xml):
        root = etree.fromstring(xml)
        if root.tag != "{C}BroadsoftDocument":
            raise ValueError
        self.logger.debug("Decoding BroadsoftDocument")
        for element in root:
            if element.tag == "command":
                command = element.get("{http://www.w3.org/2001/XMLSchema-instance}type")
                self.logger.debug(f"Decoding command {command}")
                cls = self.despatch_table[command]
                result = cls._build_from_etree(element)
                return result

    def connect(self):
        self.logger.debug(f"Attempting connection host={self.host} port={self.port}")
        try:
            address = (self.host, self.port)
            conn = socket.create_connection(address=address, timeout=self.timeout)
            self.instream = conn.makefile(mode="r")
            self.socket = conn
        except OSError as e:
            self.logger.error("Connection failed")
            raise e
        self.authenticate()

    def authenticate(self):
        self.send_command("AuthenticationRequest", user_id=self.username)
        resp = self.receive_response()
        authhash = hashlib.sha1(self.password.encode()).hexdigest().lower()
        signed_password = (
            hashlib.md5(":".join([resp.nonce, authhash]).encode()).hexdigest().lower()
        )
        self.send_command(
            "LoginRequest14sp4", user_id=self.username, signed_password=signed_password,
        )
        resp = self.receive_response()


# end