import os
import stat
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
