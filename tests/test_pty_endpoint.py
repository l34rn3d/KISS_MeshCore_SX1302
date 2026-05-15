import os
import stat
import termios
import asyncio

from sx1302_meshcore_kiss.kiss.pty_endpoint import PtyEndpoint


def test_pty_endpoint_makes_slave_readable_and_writable_for_kiss_client(tmp_path):
    link = tmp_path / "sx1302-kiss"
    endpoint = PtyEndpoint(symlink=str(link))
    try:
        asyncio.run(endpoint.start())
        mode = stat.S_IMODE(os.stat(endpoint.slave_name).st_mode)
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert mode & stat.S_IRGRP
        assert mode & stat.S_IWGRP
        assert mode & stat.S_IROTH
        assert mode & stat.S_IWOTH
    finally:
        asyncio.run(endpoint.stop())


def test_pty_endpoint_uses_raw_mode_for_binary_kiss_frames(tmp_path):
    link = tmp_path / "sx1302-kiss"
    endpoint = PtyEndpoint(symlink=str(link))
    try:
        asyncio.run(endpoint.start())
        slave_fd = os.open(endpoint.slave_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            master_attrs = termios.tcgetattr(endpoint.master_fd)
            slave_attrs = termios.tcgetattr(slave_fd)
            for attrs in (master_attrs, slave_attrs):
                iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
                assert not (lflag & termios.ICANON)
                assert not (lflag & termios.ECHO)
                assert not (iflag & termios.ICRNL)
                assert not (oflag & termios.OPOST)
        finally:
            os.close(slave_fd)
    finally:
        asyncio.run(endpoint.stop())


def test_pty_endpoint_survives_client_disconnect(tmp_path):
    link = tmp_path / "sx1302-kiss"
    endpoint = PtyEndpoint(symlink=str(link))
    try:
        asyncio.run(endpoint.start())
        slave_fd = os.open(endpoint.slave_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        os.close(slave_fd)
        assert asyncio.run(endpoint.read_bytes()) == b""
    finally:
        asyncio.run(endpoint.stop())
