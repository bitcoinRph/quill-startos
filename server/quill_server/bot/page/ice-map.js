/* Exact ICE address rewrites for a co-located Jitsi bridge. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.QuillIceMap = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function rewriteCandidate(candidate, addressMap) {
    if (!candidate || !addressMap || typeof candidate !== 'string') return candidate;
    const prefix = candidate.startsWith('a=') ? 'a=' : '';
    const body = prefix ? candidate.slice(2) : candidate;
    const match = body.match(/^(candidate:\S+\s+\d+\s+\S+\s+\d+\s+)(\S+)(\s+\d+\s+typ\s+\S+.*)$/i);
    if (!match) return candidate;
    const replacement = addressMap[match[2]];
    return replacement ? prefix + match[1] + replacement + match[3] : candidate;
  }

  function rewriteSdp(sdp, addressMap) {
    if (!sdp || !addressMap) return sdp;
    return sdp
      .split(/(\r\n|\n)/)
      .map((part) => part.startsWith('a=candidate:') ? rewriteCandidate(part, addressMap) : part)
      .join('');
  }

  function install(addressMap) {
    if (!addressMap || Object.keys(addressMap).length === 0 || typeof RTCPeerConnection === 'undefined') return;
    const proto = RTCPeerConnection.prototype;
    const nativeSetRemoteDescription = proto.setRemoteDescription;
    const nativeAddIceCandidate = proto.addIceCandidate;

    proto.setRemoteDescription = function (description, ...rest) {
      let mapped = description;
      if (description && description.sdp) {
        const init = { type: description.type, sdp: rewriteSdp(description.sdp, addressMap) };
        mapped = typeof RTCSessionDescription === 'function' ? new RTCSessionDescription(init) : init;
      }
      return nativeSetRemoteDescription.call(this, mapped, ...rest);
    };

    proto.addIceCandidate = function (candidate, ...rest) {
      let mapped = candidate;
      if (candidate && candidate.candidate) {
        const init = {
          candidate: rewriteCandidate(candidate.candidate, addressMap),
          sdpMid: candidate.sdpMid,
          sdpMLineIndex: candidate.sdpMLineIndex,
          usernameFragment: candidate.usernameFragment,
        };
        mapped = typeof RTCIceCandidate === 'function' ? new RTCIceCandidate(init) : init;
      }
      return nativeAddIceCandidate.call(this, mapped, ...rest);
    };
    if (typeof console !== 'undefined' && console.info) {
      console.info('Quill ICE address map installed (' + Object.keys(addressMap).length + ' entries)');
    }
  }

  return { rewriteCandidate, rewriteSdp, install };
});
