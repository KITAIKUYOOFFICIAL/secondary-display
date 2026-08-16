(() => {
  'use strict';
  const HTTP_URL = 'http://127.0.0.1:8080/api/inflink';
  const WS_URL = 'ws://127.0.0.1:8765';
  const log = (...args) => console.log('[secdisp-probe]', ...args);

  const waitFor = (pred, timeout = 30000) => new Promise((resolve, reject) => {
    const start = Date.now();
    const t = setInterval(() => {
      if (pred()) { clearInterval(t); resolve(); }
      else if (Date.now() - start > timeout) { clearInterval(t); reject(new Error('timeout')); }
    }, 200);
  });

  async function start() {
    log('⚡ Starting secdisp-probe plugin...');
    try {
      await waitFor(() => window.InfLinkApi, 30000);
    } catch(e) {
      log('InfLinkApi not found within timeout');
    }

    const api = window.InfLinkApi;
    if (!api) {
      log('InfLinkApi is unavailable, will retry periodically');
      setTimeout(start, 3000);
      return;
    }

    log('⚡ InfLinkApi detected successfully!');

    function extractCover(song) {
      try {
        if (song) {
          if (song.cover) {
            if (typeof song.cover === 'string' && song.cover.length > 5) return song.cover;
            if (song.cover.url && song.cover.url.length > 5) return song.cover.url;
            if (song.cover.blob && song.cover.blob.length > 5) return song.cover.blob;
          }
          if (song.picUrl) return song.picUrl;
          if (song.al && song.al.picUrl) return song.al.picUrl;
        }
        if (window.player && typeof window.player.getTrack === 'function') {
          const t = window.player.getTrack();
          if (t) {
            const p = t.picUrl || (t.album && (t.album.picUrl || t.album.cover)) || (t.al && t.al.picUrl) || t.coverUrl;
            if (p) return p;
          }
        }
        const selectors = ['.m-player .cover img', '.cd-cover img', '.j-flag.cover img', 'img[src*="music.126.net"]', 'img[src*="126.net"]'];
        for (const sel of selectors) {
          const el = document.querySelector(sel);
          if (el && el.src && el.src.includes('126.net')) return el.src;
        }
      } catch(e) {}
      return '';
    }

    let ws = null;
    let wsReady = false;

    function connectWS() {
      try {
        ws = new WebSocket(WS_URL);
        ws.onopen = () => {
          wsReady = true;
          log('⚡ Connected to Secondary Display WebSocket server');
        };
        // 接收 PC 端控制指令 (暂停/播放/切歌/跳转) — 直连 InfLinkApi 原生控制
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (!msg || msg.type !== 'ncm_command') return;
            const action = msg.action;
            const st = api.getPlaybackStatus();
            switch (action) {
              case 'play_pause': case 'toggle':
                if (st === 'Playing') api.pause(); else api.play();
                break;
              case 'play': api.play(); break;
              case 'pause': api.pause(); break;
              case 'next': api.next(); break;
              case 'prev': case 'previous': api.previous(); break;
              case 'seek': {
                const pos = parseFloat(msg.position);
                if (!isNaN(pos) && pos >= 0) api.seekTo(Math.round(pos * 1000));
                break;
              }
              default: break;
            }
            log('ncm_command executed:', action);
          } catch(e) { log('ncm_command error:', e.message); }
        };
        ws.onclose = () => {
          wsReady = false;
          setTimeout(connectWS, 2000);
        };
        ws.onerror = () => {
          wsReady = false;
        };
      } catch(e) {
        wsReady = false;
        setTimeout(connectWS, 2000);
      }
    }
    connectWS();

    const send = () => {
      try {
        const song = api.getCurrentSong();
        const tl = api.getTimeline();
        const status = api.getPlaybackStatus();
        if (!song) return;

        const coverUrl = extractCover(song);
        const data = {
          title: song.songName || '',
          artist: song.authorName || '',
          album: song.albumName || '',
          cover: coverUrl,
          duration: song.duration ? song.duration / 1000 : (tl && tl.totalTime ? tl.totalTime / 1000 : 0),
          position: tl && tl.currentTime ? tl.currentTime / 1000 : 0,
          playing: status === 'Playing',
        };

        if (wsReady && ws && ws.readyState === WebSocket.OPEN) {
          try {
            ws.send(JSON.stringify({ type: 'ncm_sync', data: data }));
          } catch(e) {}
        }

        fetch(HTTP_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        }).catch(() => {});
      } catch(e) {}
    };

    api.addEventListener('rawTimelineUpdate', send);
    api.addEventListener('timelineUpdate', send);
    api.addEventListener('songChange', send);
    api.addEventListener('playStateChange', send);
    setInterval(send, 200);
    send();
    log('⚡ Event listeners registered successfully');
  }

  start();
})();
