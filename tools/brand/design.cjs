/* Original Snowbeam vector artwork. One drawing drives icons and every motion frame. */
(function (scope) {
  "use strict";
  const DURATION = 10;
  const INK = "#091923";
  const ICE = "#a2edfa";
  const WHITE = "#effcff";
  const PERSON = `
    <circle cx="256" cy="204" r="23"/>
    <path d="M241 241 C230 241 220 249 217 261 L203 311
      C201 318 205 323 211 323 C216 323 219 320 221 315 L234 277
      L233 326 L223 378 C221 386 225 392 232 392
      C238 392 242 388 244 381 L255 337 L257 337 L268 381
      C270 388 274 392 280 392 C287 392 291 386 289 378
      L279 326 L278 277 L291 315 C293 320 296 323 301 323
      C307 323 311 318 309 311 L295 261 C292 249 282 241 271 241 Z"/>
  `;
  const FLAKE = `<path d="M0-14V14 M-12.12-7L12.12 7 M-12.12 7L12.12-7
    M-4-10L0-6 4-10 M-4 10L0 6 4 10
    M-10.7-1.2L-5.2-3 -6.7-8.5 M10.7 1.2L5.2 3 6.7 8.5
    M-6.7 8.5L-5.2 3 -10.7 1.2 M6.7-8.5L5.2-3 10.7-1.2"/>
  `;
  const flakes = [
    [200, 151, 0.55, 4, 0], [276, 139, 0.68, -7, .13],
    [240, 207, .9, 8, .18], [302, 230, .55, 13, .36],
    [194, 281, .76, -11, .25], [274, 305, .8, 10, .5],
    [242, 352, .52, -12, .25], [325, 175, .44, -6, .4],
    [212, 224, .4, 9, .59], [286, 267, .4, -7, .3],
    [252, 157, .38, 5, .68], [305, 329, .43, -6, .1],
  ];
  const clamp = x => Math.max(0, Math.min(1, x));
  const ease = x => { const n = clamp(x); return n * n * (3 - 2 * n); };
  const ramp = (t, a, b) => ease((t - a) / (b - a));
  const number = x => Number(x.toFixed(4));
  const opacity = x => `opacity="${number(clamp(x))}"`;

  function render(time = 1.85, { badge = true, poster = false, id = "snowbeam" } = {}) {
    const t = Math.max(0, Math.min(DURATION, time));
    const beamIn = ramp(t, .9, 1.9);
    const beamOut = ramp(t, 2.9, 3.4);
    const beam = beamIn * (1 - beamOut);
    const person = (1 - ramp(t, 2.05, 2.95)) + ramp(t, 8.35, 9.1);
    const lift = -34 * ramp(t, 2.05, 2.95) * (1 - ramp(t, 8.0, 8.35));
    const halo = .28 * person + .7 * beam;
    const bx = 1 - .92 * beamOut;
    const defs = `<defs>
      <linearGradient id="${id}-beam-fill" x1="0" y1="0" x2="0" y2="1">
        <stop stop-color="#97edff" stop-opacity=".05"/>
        <stop offset=".5" stop-color="#62d8ef" stop-opacity=".24"/>
        <stop offset="1" stop-color="#63d7ed" stop-opacity=".1"/>
      </linearGradient>
      <linearGradient id="${id}-edge" x1="0" y1="0" x2="0" y2="1">
        <stop stop-color="#a2edfa" stop-opacity=".25"/>
        <stop offset=".55" stop-color="#a2edfa"/>
        <stop offset="1" stop-color="#a2edfa" stop-opacity=".3"/>
      </linearGradient>
      <linearGradient id="${id}-light" x1="0" y1="0" x2="0" y2="1">
        <stop stop-color="#edfcff"/><stop offset="1" stop-color="#c2eff5"/>
      </linearGradient>
    </defs>`;
    const snowfall = poster ? "" : flakes.map(([x, y, size, sway, delay], i) => {
      const age = t - 3.46 - delay;
      const progress = clamp(age / 3.6);
      const visible = ramp(age, 0, .4) * (1 - ramp(age, 2.8, 3.8));
      const px = x + Math.sin(progress * Math.PI * 1.3) * sway;
      const py = y + progress * (99 + i % 3 * 12);
      const turn = (i % 2 ? 1 : -1) * progress * 34;
      return `<g ${opacity(visible)} transform="translate(${number(px)} ${number(py)}) rotate(${number(turn)}) scale(${size})">${FLAKE}</g>`;
    }).join("");
    const spark = poster ? `<g transform="translate(350 179) scale(1.22)" stroke="${ICE}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round">${FLAKE}</g>` : "";
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" role="img" aria-labelledby="${id}-title ${id}-desc">
      <title id="${id}-title">Snowbeam</title>
      <desc id="${id}-desc">A standing person enveloped by an icy blue transporter beam; after they vanish, snowflakes drift down.</desc>
      ${defs}
      ${badge ? `<rect x="8" y="8" width="496" height="496" rx="112" fill="${INK}"/>
      <rect x="9" y="9" width="494" height="494" rx="111" fill="none" stroke="#23404e" stroke-opacity=".65" stroke-width="2"/>` : ""}
      <g id="${id}-ground" ${opacity(halo)} fill="none" stroke="${ICE}">
        <ellipse cx="256" cy="407" rx="107" ry="18" stroke-width="4"/>
        <ellipse cx="256" cy="407" rx="74" ry="10" stroke-width="1.5" opacity=".3"/>
      </g>
      <g id="${id}-beam" ${opacity(beam)} transform="translate(${number(256 * (1 - bx))} 0) scale(${number(bx)} 1)">
        <path d="M181 110 Q256 82 331 110 L362 400 Q256 437 150 400Z" fill="url(#${id}-beam-fill)"/>
        <path d="M181 110L150 399 M331 110L362 399" fill="none" stroke="url(#${id}-edge)" stroke-width="5" stroke-linecap="round"/>
        <path d="M206 111L191 384 M231 112L226 366 M281 112L286 366 M306 111L321 384" fill="none" stroke="${ICE}" stroke-width="2" opacity=".18"/>
        <ellipse cx="256" cy="110" rx="75" ry="15" fill="none" stroke="${ICE}" stroke-width="4" opacity=".8"/>
      </g>
      <g id="${id}-person" ${opacity(person)} transform="translate(0 ${number(lift)})" fill="url(#${id}-light)">${PERSON}</g>
      ${spark}
      <g id="${id}-snow" stroke="${WHITE}" stroke-width="2.7" fill="none" stroke-linecap="round" stroke-linejoin="round">${snowfall}</g>
    </svg>`.replace(/[ \t]+$/gm, "");
  }
  const api = { render, duration: DURATION, colors: { ink: INK, ice: ICE, white: WHITE } };
  if (typeof module !== "undefined") module.exports = api;
  else scope.SnowbeamBrand = api;
})(typeof window === "undefined" ? {} : window);
