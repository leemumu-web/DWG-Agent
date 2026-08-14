export function createRequestKey(): string {
  // 幂等键：前两分支按 RFC 4122 构造 UUID v4（bytes[6] 设 version=4、
  // bytes[8] 设 variant=10 位），供服务器端幂等去重。
  // 回退分支返回「时间戳-随机数」，不是 UUID 格式——需确认服务器端对
  // key 格式的约束，若按 UUID 解析会破坏去重契约。
  const browserCrypto = globalThis.crypto;
  if (typeof browserCrypto?.randomUUID === 'function') return browserCrypto.randomUUID();
  if (typeof browserCrypto?.getRandomValues === 'function') {
    const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
