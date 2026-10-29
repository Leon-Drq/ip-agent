"""Generate the project's own simple play-mark icon without a design dependency."""
import math
import struct
import zlib
from pathlib import Path
size=512
pixels=[]
for y in range(size):
    row=bytearray()
    for x in range(size):
        dx=max(40-x,0,x-471);dy=max(40-y,0,y-471)
        inside=24<=x<=487 and 24<=y<=487
        # Rounded cyan square and a white outlined play glyph.
        qx=max(abs(x-256)-140,0);qy=max(abs(y-256)-140,0)
        inside=qx*qx+qy*qy<=92*92
        outer=181<=x<=361 and abs(y-256)<= (361-x)*.65
        inner=198<=x<=326 and abs(y-256)<=(326-x)*.60
        row.extend((255,255,255,255) if outer and not inner else (4,181,213,255) if inside else (0,0,0,0))
    pixels.append(b'\0'+row)
def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(pixels)))+chunk(b'IEND',b'')
folder=Path(__file__).resolve().parent.parent/'assets';folder.mkdir(exist_ok=True);(folder/'icon.png').write_bytes(data)
