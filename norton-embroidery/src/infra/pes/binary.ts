/** Little-endian binary reader/writer used by the PES and DST codecs. */

export class BinaryWriter {
  private buf: Uint8Array;
  private len = 0;

  constructor(initialCapacity = 4096) {
    this.buf = new Uint8Array(initialCapacity);
  }

  get length(): number {
    return this.len;
  }

  private ensure(extra: number): void {
    if (this.len + extra <= this.buf.length) return;
    let cap = this.buf.length * 2;
    while (cap < this.len + extra) cap *= 2;
    const next = new Uint8Array(cap);
    next.set(this.buf.subarray(0, this.len));
    this.buf = next;
  }

  u8(value: number): this {
    this.ensure(1);
    this.buf[this.len++] = value & 0xff;
    return this;
  }

  bytes(values: ArrayLike<number>): this {
    this.ensure(values.length);
    this.buf.set(values instanceof Uint8Array ? values : Uint8Array.from(values as number[]), this.len);
    this.len += values.length;
    return this;
  }

  u16(value: number): this {
    this.ensure(2);
    this.buf[this.len++] = value & 0xff;
    this.buf[this.len++] = (value >> 8) & 0xff;
    return this;
  }

  u24(value: number): this {
    this.ensure(3);
    this.buf[this.len++] = value & 0xff;
    this.buf[this.len++] = (value >> 8) & 0xff;
    this.buf[this.len++] = (value >> 16) & 0xff;
    return this;
  }

  u32(value: number): this {
    this.ensure(4);
    this.buf[this.len++] = value & 0xff;
    this.buf[this.len++] = (value >>> 8) & 0xff;
    this.buf[this.len++] = (value >>> 16) & 0xff;
    this.buf[this.len++] = (value >>> 24) & 0xff;
    return this;
  }

  f32(value: number): this {
    this.ensure(4);
    const view = new DataView(new ArrayBuffer(4));
    view.setFloat32(0, value, true);
    for (let i = 0; i < 4; i++) this.buf[this.len++] = view.getUint8(i);
    return this;
  }

  /** ASCII string, no length prefix and no terminator. */
  ascii(value: string): this {
    for (let i = 0; i < value.length; i++) this.u8(value.charCodeAt(i) & 0xff);
    return this;
  }

  /** Length-prefixed string with a 1-byte length. */
  pesString8(value: string | null | undefined): this {
    if (value === null || value === undefined) return this.u8(0);
    const s = value.length > 255 ? value.slice(0, 255) : value;
    this.u8(s.length);
    return this.ascii(s);
  }

  /** Length-prefixed string with a 2-byte length. */
  pesString16(value: string | null | undefined): this {
    if (value === null || value === undefined) return this.u16(0);
    this.u16(value.length);
    return this.ascii(value);
  }

  /** Current write position, for later patching. */
  tell(): number {
    return this.len;
  }

  /** Overwrite 4 bytes at `position` with a little-endian uint32. */
  patchU32(position: number, value: number): void {
    this.buf[position] = value & 0xff;
    this.buf[position + 1] = (value >>> 8) & 0xff;
    this.buf[position + 2] = (value >>> 16) & 0xff;
    this.buf[position + 3] = (value >>> 24) & 0xff;
  }

  patchU24(position: number, value: number): void {
    this.buf[position] = value & 0xff;
    this.buf[position + 1] = (value >> 8) & 0xff;
    this.buf[position + 2] = (value >> 16) & 0xff;
  }

  patchU16(position: number, value: number): void {
    this.buf[position] = value & 0xff;
    this.buf[position + 1] = (value >> 8) & 0xff;
  }

  toUint8Array(): Uint8Array {
    return this.buf.slice(0, this.len);
  }
}

export class BinaryReader {
  private pos = 0;

  constructor(private readonly data: Uint8Array) {}

  get position(): number {
    return this.pos;
  }

  get remaining(): number {
    return this.data.length - this.pos;
  }

  seek(position: number): void {
    this.pos = position;
  }

  skip(count: number): void {
    this.pos += count;
  }

  u8(): number {
    if (this.pos >= this.data.length) throw new RangeError('Unexpected end of file');
    return this.data[this.pos++];
  }

  u16(): number {
    return this.u8() | (this.u8() << 8);
  }

  i16(): number {
    const v = this.u16();
    return v >= 0x8000 ? v - 0x10000 : v;
  }

  u24(): number {
    return this.u8() | (this.u8() << 8) | (this.u8() << 16);
  }

  u32(): number {
    return (this.u8() | (this.u8() << 8) | (this.u8() << 16) | (this.u8() << 24)) >>> 0;
  }

  ascii(length: number): string {
    let s = '';
    for (let i = 0; i < length; i++) s += String.fromCharCode(this.u8());
    return s;
  }

  peek(offset = 0): number {
    return this.data[this.pos + offset];
  }
}
