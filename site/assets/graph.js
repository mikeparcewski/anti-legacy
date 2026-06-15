/* ============================================================================
   anti-legacy — graph.js
   "Forensic Modernism" live code→graph build animation.

   A single <canvas> + requestAnimationFrame force-directed simulation drives a
   four-act state machine over ONE shared graph model:

     ACT 1  INDEX     real COBOL/JCL source ignites line-by-line; symbols fly out
                      as particles and settle into a living AMBER code graph.
     ACT 2  ANNOTATE  an adaptive ring-expansion sweep writes a business rule onto
                      each node; nodes resolve (cyan) or risk-flag (terracotta).
     ACT 3  COVER     the field becomes a ledger; an "accounted = 100%" meter fills.
     ACT 4  RE-THINK  positions tween legacy→domain, color crossfades amber→cyan,
                      legacy nodes merge into capability requirements, faint
                      traceability threads persist (covers-every-requirement).

   Nodes are absolutely-positioned rounded-rect DOM elements (crisp text); the
   canvas draws only bezier edges, arrowheads and transit particles (cheap).

   Public API:
     const g = AntiLegacyGraph.mount(rootEl, options);
     g.play(actIndex)   g.replay()   g.goTo(actIndex)   g.destroy()
   Emits 'antilegacy:act' CustomEvents on rootEl as acts change.

   Self-contained: no dependencies. Honors prefers-reduced-motion.
   ========================================================================== */
(function (global) {
  'use strict';

  /* ---- tokens mirrored from style.css (kept in sync; canvas can't read vars
         cheaply per-frame, so we resolve once at mount and cache) ---------- */
  var FALLBACK = {
    amberCore: '#C9A227', amberBright: '#E6BC3A',
    amberDim: 'rgba(201,162,39,0.16)', amberGlow: 'rgba(201,162,39,0.45)',
    cyanCore: '#3DE0C6', cyanBright: '#6BF0DC',
    cyanDim: 'rgba(61,224,198,0.16)', cyanGlow: 'rgba(61,224,198,0.5)',
    risk: '#E5734B', riskDim: 'rgba(229,115,75,0.18)',
    hairline: '#232934', fog: '#7E8896', ink: '#0A0C10'
  };

  var EASE = {
    settle: function (t) { return cubicBezier(t, 0.16, 1, 0.3, 1); },
    outSoft: function (t) { return cubicBezier(t, 0.22, 1, 0.36, 1); },
    inOut: function (t) { return cubicBezier(t, 0.65, 0.05, 0.36, 1); }
  };

  var ACTS = ['index', 'annotate', 'cover', 'rethink'];

  /* The full estate node count reported by the INDEX HUD. The on-stage graph is
     a curated, legible slice (~16 nodes); this is the headline estate total that
     the synthetic index ramp counts up to and that the settled INDEX view shows. */
  var ESTATE_NODE_COUNT = 10307;

  /* =========================================================================
     DATA — a real, hand-authored slice of the carddemo estate.
     node kinds: module | paragraph | dataset | cics_program | db2_table
     Each node carries legacy + domain cluster ids so two layouts can coexist.
     ====================================================================== */
  function buildModel() {
    // legacy clusters (by module) and domain clusters (by capability)
    var nodes = [
      // --- CBSTM03B statement subsystem (module) ---
      { id: 'CBSTM03B', label: 'CBSTM03B', tag: 'MOD', kind: 'module',
        line: 12, lc: 'stmt', dc: 'cap-statement',
        rule: 'Drive statement-file generation per account cycle.',
        conf: 0.94, prov: 'CBSTM03B:1', state: 'resolved' },
      { id: '1000-MAIN', label: '1000-MAINLINE', tag: 'PARA', kind: 'paragraph',
        line: 41, lc: 'stmt', dc: 'cap-statement',
        rule: 'Open files, loop accounts, close — orchestration spine.',
        conf: 0.91, prov: 'CBSTM03B:412', state: 'resolved' },
      { id: '2000-READ', label: '2000-READ-XREF', tag: 'PARA', kind: 'paragraph',
        line: 88, lc: 'stmt', dc: 'cap-statement',
        rule: 'Read card-xref; reject INVALID_ACTION + negative amount → 400.',
        conf: 0.88, prov: 'CBSTM03B:1180', state: 'resolved' },
      { id: 'STMT-FILE', label: 'STMT.OUT', tag: 'DS', kind: 'dataset',
        line: 0, lc: 'stmt', dc: 'cap-statement',
        rule: 'Golden-file output; COMP-3 precision to 2 places.',
        conf: 0.86, prov: 'CBSTM03B.jcl:STEP04', state: 'risk' },
      { id: 'TRNX-DB2', label: 'TRANSACT', tag: 'DB2', kind: 'db2_table',
        line: 0, lc: 'stmt', dc: 'cap-statement',
        rule: 'Source of monetary aggregates for the cycle.',
        conf: 0.83, prov: 'DB2:TRANSACT', state: 'resolved' },

      // --- COACTVWC account view (cics_program) ---
      { id: 'COACTVWC', label: 'COACTVWC', tag: 'CICS', kind: 'cics_program',
        line: 7, lc: 'acct', dc: 'cap-account',
        rule: 'CICS account-view transaction; validates acct id then reads.',
        conf: 0.90, prov: 'COACTVWC:1', state: 'resolved' },
      { id: 'VIEW-ACCT', label: '0000-VIEW-ACCT', tag: 'PARA', kind: 'paragraph',
        line: 120, lc: 'acct', dc: 'cap-account',
        rule: 'Fetch ACCTDAT by id; map to BMS screen.',
        conf: 0.87, prov: 'COACTVWC:402', state: 'resolved' },
      { id: 'ACCTDAT', label: 'ACCTDAT', tag: 'DS', kind: 'dataset',
        line: 0, lc: 'acct', dc: 'cap-account',
        rule: 'VSAM account master keyed by 11-digit acct id.',
        conf: 0.89, prov: 'JCL:ACCTDAT DD', state: 'resolved' },
      { id: 'ACCT-DB2', label: 'ACCOUNT', tag: 'DB2', kind: 'db2_table',
        line: 0, lc: 'acct', dc: 'cap-account',
        rule: 'Relational mirror of account master.',
        conf: 0.84, prov: 'DB2:ACCOUNT', state: 'resolved' },

      // --- COTRN01C transaction add (cics_program) ---
      { id: 'COTRN01C', label: 'COTRN01C', tag: 'CICS', kind: 'cics_program',
        line: 7, lc: 'trn', dc: 'cap-transaction',
        rule: 'Add-transaction screen flow; posts to TRANSACT.',
        conf: 0.88, prov: 'COTRN01C:1', state: 'resolved' },
      { id: 'ADD-TRAN', label: '1000-ADD-TRAN', tag: 'PARA', kind: 'paragraph',
        line: 96, lc: 'trn', dc: 'cap-transaction',
        rule: 'Validate amount sign + limits; ERROR_ACTION → 500.',
        conf: 0.79, prov: 'COTRN01C:511', state: 'risk' },
      { id: 'TRAN-DS', label: 'DALYTRAN', tag: 'DS', kind: 'dataset',
        line: 0, lc: 'trn', dc: 'cap-transaction',
        rule: 'Daily transaction staging file.',
        conf: 0.85, prov: 'JCL:DALYTRAN DD', state: 'resolved' },

      // --- COBIL00C billing (cics_program) ---
      { id: 'COBIL00C', label: 'COBIL00C', tag: 'CICS', kind: 'cics_program',
        line: 7, lc: 'bill', dc: 'cap-billing',
        rule: 'Bill-pay screen; debits account, writes transaction.',
        conf: 0.86, prov: 'COBIL00C:1', state: 'resolved' },
      { id: 'BILL-PAY', label: '2000-BILL-PAY', tag: 'PARA', kind: 'paragraph',
        line: 140, lc: 'bill', dc: 'cap-billing',
        rule: 'Compute payoff; COMP-3 rounding to 2 places.',
        conf: 0.82, prov: 'COBIL00C:702', state: 'resolved' },

      // --- shared / cross-domain date util (module) ---
      { id: 'CSUTLDTC', label: 'CSUTLDTC', tag: 'MOD', kind: 'module',
        line: 3, lc: 'util', dc: 'cap-platform',
        rule: 'Common date-validation subroutine (CEEDAYS wrapper).',
        conf: 0.93, prov: 'CSUTLDTC:1', state: 'resolved' },
      { id: 'XREF-DS', label: 'CARDXREF', tag: 'DS', kind: 'dataset',
        line: 0, lc: 'util', dc: 'cap-platform',
        rule: 'Card-to-account cross reference.',
        conf: 0.88, prov: 'JCL:CARDXREF DD', state: 'resolved' }
    ];

    var edges = [
      { from: 'CBSTM03B', to: '1000-MAIN', conf: 0.94, kind: 'calls' },
      { from: '1000-MAIN', to: '2000-READ', conf: 0.91, kind: 'calls' },
      { from: '2000-READ', to: 'XREF-DS', conf: 0.90, kind: 'reads' },
      { from: '2000-READ', to: 'TRNX-DB2', conf: 0.83, kind: 'reads' },
      { from: '1000-MAIN', to: 'STMT-FILE', conf: 0.86, kind: 'writes' },
      { from: 'CBSTM03B', to: 'CSUTLDTC', conf: 0.80, kind: 'calls' },

      { from: 'COACTVWC', to: 'VIEW-ACCT', conf: 0.90, kind: 'calls' },
      { from: 'VIEW-ACCT', to: 'ACCTDAT', conf: 0.89, kind: 'reads' },
      { from: 'VIEW-ACCT', to: 'ACCT-DB2', conf: 0.84, kind: 'reads' },
      { from: 'COACTVWC', to: 'CSUTLDTC', conf: 0.78, kind: 'calls' },

      { from: 'COTRN01C', to: 'ADD-TRAN', conf: 0.88, kind: 'calls' },
      { from: 'ADD-TRAN', to: 'TRAN-DS', conf: 0.85, kind: 'writes' },
      { from: 'ADD-TRAN', to: 'TRNX-DB2', conf: 0.81, kind: 'writes' },
      { from: 'ADD-TRAN', to: 'ACCT-DB2', conf: 0.77, kind: 'reads' },

      { from: 'COBIL00C', to: 'BILL-PAY', conf: 0.86, kind: 'calls' },
      { from: 'BILL-PAY', to: 'ACCT-DB2', conf: 0.80, kind: 'writes' },
      { from: 'BILL-PAY', to: 'TRNX-DB2', conf: 0.82, kind: 'writes' },
      { from: 'COBIL00C', to: 'CSUTLDTC', conf: 0.76, kind: 'calls' }
    ];

    // domain (target) requirement nodes + the legacy nodes that merge into them.
    // Domain graph is organized by capability; some legacy nodes converge.
    var domain = [
      { id: 'REQ_CBSTM03B', label: 'REQ_CBSTM03B', tag: 'REQ', cap: 'cap-statement',
        target: 'Cbstm03bJob',
        rule: 'Statement service. parity golden-file; COMP-3 2dp.',
        covers: ['CBSTM03B', '1000-MAIN', '2000-READ', 'STMT-FILE', 'TRNX-DB2'] },
      { id: 'REQ_COACTVWC', label: 'REQ_COACTVWC', tag: 'REQ', cap: 'cap-account',
        target: 'CoactvwcController',
        rule: 'Account-view query. field-exact parity.',
        covers: ['COACTVWC', 'VIEW-ACCT', 'ACCTDAT', 'ACCT-DB2'] },
      { id: 'REQ_COTRN01C', label: 'REQ_COTRN01C', tag: 'REQ', cap: 'cap-transaction',
        target: 'TransactionService',
        rule: 'Post transaction. validation + error parity.',
        covers: ['COTRN01C', 'ADD-TRAN', 'TRAN-DS'] },
      { id: 'REQ_COBIL00C', label: 'REQ_COBIL00C', tag: 'REQ', cap: 'cap-billing',
        target: 'BillPayService',
        rule: 'Bill payment. monetary precision parity.',
        covers: ['COBIL00C', 'BILL-PAY'] },
      { id: 'REQ_PLATFORM', label: 'REQ_PLATFORM', tag: 'REQ', cap: 'cap-platform',
        target: 'PlatformCommons',
        rule: 'Shared date/xref utilities, extracted once.',
        covers: ['CSUTLDTC', 'XREF-DS'] }
    ];

    var domainEdges = [
      { from: 'REQ_COTRN01C', to: 'REQ_COBIL00C', conf: 0.9, kind: 'depends' },
      { from: 'REQ_COBIL00C', to: 'REQ_COACTVWC', conf: 0.9, kind: 'depends' },
      { from: 'REQ_CBSTM03B', to: 'REQ_COACTVWC', conf: 0.9, kind: 'depends' },
      { from: 'REQ_CBSTM03B', to: 'REQ_PLATFORM', conf: 0.85, kind: 'uses' },
      { from: 'REQ_COTRN01C', to: 'REQ_PLATFORM', conf: 0.85, kind: 'uses' }
    ];

    // map legacy id -> domain id (for traceability threads)
    var trace = {};
    domain.forEach(function (d) {
      d.covers.forEach(function (legacyId) { trace[legacyId] = d.id; });
    });

    return { nodes: nodes, edges: edges, domain: domain, domainEdges: domainEdges, trace: trace };
  }

  /* real source lines for the ignite panel (COBOL / JCL / DB2) */
  var SOURCE_LINES = [
    { t: 'IDENTIFICATION DIVISION.', n: 'CBSTM03B' },
    { t: 'PROGRAM-ID. CBSTM03B.', n: 'CBSTM03B' },
    { t: 'PROCEDURE DIVISION.', n: '1000-MAIN' },
    { t: ' 1000-MAINLINE.', n: '1000-MAIN' },
    { t: '   PERFORM 2000-READ-XREF', n: '2000-READ' },
    { t: '   READ CARDXREF INTO WS-XREF', n: 'XREF-DS' },
    { t: '   EXEC SQL SELECT * FROM TRANSACT', n: 'TRNX-DB2' },
    { t: '   WRITE STMT-REC TO STMT-OUT', n: 'STMT-FILE' },
    { t: ' EXEC CICS RECEIVE MAP(COACTVW)', n: 'COACTVWC' },
    { t: '   PERFORM 0000-VIEW-ACCT', n: 'VIEW-ACCT' },
    { t: '   READ ACCTDAT KEY IS WS-ACCT-ID', n: 'ACCTDAT' },
    { t: '   EXEC SQL SELECT FROM ACCOUNT', n: 'ACCT-DB2' },
    { t: ' EXEC CICS RECEIVE MAP(COTRN01)', n: 'COTRN01C' },
    { t: '   PERFORM 1000-ADD-TRAN', n: 'ADD-TRAN' },
    { t: '   WRITE DALYTRAN-REC', n: 'TRAN-DS' },
    { t: ' EXEC CICS RECEIVE MAP(COBIL00)', n: 'COBIL00C' },
    { t: '   PERFORM 2000-BILL-PAY', n: 'BILL-PAY' },
    { t: '   CALL "CSUTLDTC" USING WS-DATE', n: 'CSUTLDTC' },
    { t: '//STEP04 DD DSN=AWS.STMT.OUT', n: 'STMT-FILE' },
    { t: '//ACCTDAT DD DSN=AWS.VSAM.ACCT', n: 'ACCTDAT' }
  ];

  /* =========================================================================
     UTILITIES
     ====================================================================== */
  function cubicBezier(t, x1, y1, x2, y2) {
    // approximate the y for a given t along a cubic-bezier(x1,y1,x2,y2) easing
    if (t <= 0) return 0; if (t >= 1) return 1;
    var u = t;
    for (var i = 0; i < 6; i++) {
      var x = 3 * (1 - u) * (1 - u) * u * x1 + 3 * (1 - u) * u * u * x2 + u * u * u;
      var d = 3 * (1 - u) * (1 - u) * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u * u * (1 - x2);
      if (Math.abs(d) < 1e-6) break;
      u -= (x - t) / d;
      u = Math.max(0, Math.min(1, u));
    }
    return 3 * (1 - u) * (1 - u) * u * y1 + 3 * (1 - u) * u * u * y2 + u * u * u;
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function now() { return (global.performance && performance.now) ? performance.now() : Date.now(); }
  function hexToRgb(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function mixHex(a, b, t) {
    var ca = hexToRgb(a), cb = hexToRgb(b);
    return 'rgb(' + Math.round(lerp(ca[0], cb[0], t)) + ',' +
      Math.round(lerp(ca[1], cb[1], t)) + ',' + Math.round(lerp(ca[2], cb[2], t)) + ')';
  }
  function prefersReducedMotion() {
    return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function readToken(styles, name, fb) {
    var v = styles.getPropertyValue(name);
    return v && v.trim() ? v.trim() : fb;
  }

  /* =========================================================================
     GRAPH INSTANCE
     ====================================================================== */
  function GraphInstance(root, opts) {
    this.root = root;
    this.opts = opts || {};
    this.model = buildModel();
    this.reduced = prefersReducedMotion();
    this.timers = [];
    this.raf = null;
    this.running = false;
    this.act = 'index';
    this.actIndex = 0;
    this.tokens = {};
    this.nodes = [];         // runtime node objects
    this.nodeById = {};
    this.liveEdges = [];     // edges currently drawn
    this.particles = [];
    this.threads = [];       // traceability threads (act 4)
    this.coverProgress = 0;  // 0..1 act3 meter
    this.morph = 0;          // 0..1 act4 legacy->domain morph
    this.visible = true;
    this._mount();
  }

  GraphInstance.prototype._mount = function () {
    var r = this.root;
    r.classList.add('alg');
    r.setAttribute('data-alg', '');
    // resolve tokens
    var cs = global.getComputedStyle(document.documentElement);
    var T = this.tokens = {
      amberCore: readToken(cs, '--amber-core', FALLBACK.amberCore),
      amberBright: readToken(cs, '--amber-bright', FALLBACK.amberBright),
      amberDim: readToken(cs, '--amber-dim', FALLBACK.amberDim),
      amberGlow: readToken(cs, '--amber-glow', FALLBACK.amberGlow),
      cyanCore: readToken(cs, '--cyan-core', FALLBACK.cyanCore),
      cyanBright: readToken(cs, '--cyan-bright', FALLBACK.cyanBright),
      cyanDim: readToken(cs, '--cyan-dim', FALLBACK.cyanDim),
      cyanGlow: readToken(cs, '--cyan-glow', FALLBACK.cyanGlow),
      risk: readToken(cs, '--risk', FALLBACK.risk),
      hairline: readToken(cs, '--hairline', FALLBACK.hairline),
      fog: readToken(cs, '--fog-500', FALLBACK.fog)
    };

    // structure
    var stage = document.createElement('div');
    stage.className = 'alg-stage';

    // source panel (act 1)
    var src = document.createElement('div');
    src.className = 'alg-source';
    src.setAttribute('aria-hidden', 'true');
    var code = document.createElement('div');
    code.className = 'alg-source-code';
    SOURCE_LINES.forEach(function (l, i) {
      var line = document.createElement('div');
      line.className = 'alg-srcline';
      line.dataset.node = l.n;
      var g = document.createElement('span'); g.className = 'alg-gutter';
      g.textContent = String(i + 1).padStart(2, '0');
      var c = document.createElement('span'); c.className = 'alg-srctext';
      c.textContent = l.t;
      line.appendChild(g); line.appendChild(c);
      code.appendChild(line);
    });
    src.appendChild(code);

    // graph field
    var field = document.createElement('div');
    field.className = 'alg-field';
    var canvas = document.createElement('canvas');
    canvas.className = 'alg-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    var layer = document.createElement('div');
    layer.className = 'alg-nodes';
    layer.setAttribute('aria-hidden', 'true');
    field.appendChild(canvas);
    field.appendChild(layer);

    stage.appendChild(src);
    stage.appendChild(field);

    // counters
    var hud = document.createElement('div');
    hud.className = 'alg-hud';
    hud.setAttribute('aria-hidden', 'true');
    hud.innerHTML =
      '<span class="alg-hud-item" data-hud="nodes">nodes 0</span>' +
      '<span class="alg-hud-sep">/</span>' +
      '<span class="alg-hud-item" data-hud="time">0.0s</span>' +
      '<span class="alg-hud-sep">/</span>' +
      '<span class="alg-hud-item" data-hud="mode">INDEX · COBOL→JCL</span>';

    // controls
    var controls = document.createElement('div');
    controls.className = 'alg-controls';
    var seg = document.createElement('div');
    seg.className = 'alg-seg';
    seg.setAttribute('role', 'tablist');
    seg.setAttribute('aria-label', 'Pipeline stage');
    var labels = ['INDEX', 'ANNOTATE', 'COVER', 'RE-THINK'];
    var self = this;
    labels.forEach(function (lab, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'alg-seg-btn';
      b.setAttribute('role', 'tab');
      b.dataset.act = String(i);
      b.textContent = lab;
      b.addEventListener('click', function () { self.goTo(i); });
      seg.appendChild(b);
    });
    var replay = document.createElement('button');
    replay.type = 'button';
    replay.className = 'alg-replay';
    replay.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" width="13" height="13">' +
      '<path d="M8 2.5a5.5 5.5 0 1 0 5.32 4.1" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>' +
      '<path d="M8 0.5 11 3 8 5.5z" fill="currentColor"/></svg><span>replay</span>';
    replay.addEventListener('click', function () { self.replay(); });

    controls.appendChild(seg);
    controls.appendChild(replay);

    // a11y mirror: describe the graph for screen readers
    var sr = document.createElement('ul');
    sr.className = 'alg-sr';
    this.model.nodes.forEach(function (n) {
      var li = document.createElement('li');
      li.textContent = n.label + ' (' + n.kind.replace('_', ' ') + ', ' + n.state + '): ' + n.rule;
      sr.appendChild(li);
    });

    r.appendChild(controls);
    r.appendChild(stage);
    r.appendChild(hud);
    r.appendChild(sr);

    this.el = { stage: stage, src: src, code: code, field: field, canvas: canvas,
      layer: layer, hud: hud, seg: seg, replay: replay };
    this.ctx = canvas.getContext('2d');

    this._buildNodes();
    this._sizeCanvas();

    // resize handling
    var ro;
    if (global.ResizeObserver) {
      ro = new ResizeObserver(this._onResize.bind(this));
      ro.observe(field);
      this._ro = ro;
    } else {
      this._winResize = this._onResize.bind(this);
      global.addEventListener('resize', this._winResize);
    }

    // pause when offscreen; and DEFER the first auto-chain build until the
    // field is actually scrolled into view, so every visitor sees the ignite
    // from the top regardless of where the hero sits relative to the fold.
    this._deferredStart = false;
    if (global.IntersectionObserver) {
      var io = new IntersectionObserver(function (entries) {
        self.visible = entries[0].isIntersecting;
        if (self.visible) {
          if (self._deferredStart) { self._deferredStart = false; self.play(0); }
          else if (self.running && !self.raf && !self.reduced) self._loop();
        }
      }, { threshold: 0.05 });
      io.observe(field);
      this._io = io;
    }

    // initial render
    if (this.opts.autoplay !== false) {
      if (this.reduced) {
        this._renderStaticAct(0);
      } else if (this._io) {
        // wait for first visibility before igniting, so every visitor catches
        // the particle-transit ignite from the very top. Hold a clean pre-build
        // baseline (nodes hidden, canvas cleared) until the field scrolls in.
        this._deferredStart = true;
        this._setActUI(0);
        this._resetRuntime();
        this._paint(true);
      } else {
        this.play(0);
      }
    } else {
      this._renderStaticAct(0);
    }
  };

  /* build runtime node objects from model, with both layouts ---------------- */
  GraphInstance.prototype._buildNodes = function () {
    var self = this;
    this.nodes = [];
    this.nodeById = {};
    this.model.nodes.forEach(function (def) {
      var el = document.createElement('div');
      el.className = 'alg-node n-' + def.kind;
      el.dataset.id = def.id;
      el.dataset.state = def.state;
      el.innerHTML =
        '<span class="alg-node-tag"><i class="alg-dot"></i>' + def.tag + '</span>' +
        '<span class="alg-node-label">' + def.label + '</span>' +
        '<span class="alg-node-rule" aria-hidden="true">' + def.rule + '</span>';
      self.el.layer.appendChild(el);
      var node = {
        def: def, el: el, id: def.id,
        x: 0, y: 0, vx: 0, vy: 0,
        tx: 0, ty: 0,          // current target (layout)
        lx: 0, ly: 0,          // legacy target (computed on size)
        dx: 0, dy: 0,          // domain target (computed on size)
        w: 0, h: 0,
        placed: false,         // act1: has node arrived
        state: 'pending',      // pending|resolved|risk
        revealAt: 0,
        annotated: false,
        domainHost: self.model.trace[def.id] || null
      };
      self.nodes.push(node);
      self.nodeById[def.id] = node;
    });
    // domain (requirement) nodes — created as runtime nodes too, hidden until act4
    this.domainNodes = [];
    this.domainById = {};
    this.model.domain.forEach(function (d) {
      var el = document.createElement('div');
      el.className = 'alg-node n-req is-domain';
      el.dataset.id = d.id;
      el.style.opacity = '0';
      el.innerHTML =
        '<span class="alg-node-tag"><i class="alg-dot"></i>' + d.tag + '</span>' +
        '<span class="alg-node-label">' + d.label + '</span>' +
        '<span class="alg-node-rule" aria-hidden="true">→ ' + d.target + '</span>';
      self.el.layer.appendChild(el);
      var dn = { def: d, el: el, id: d.id, x: 0, y: 0, w: 0, h: 0, vis: 0 };
      self.domainNodes.push(dn);
      self.domainById[d.id] = dn;
    });
  };

  /* compute legacy + domain target positions for the current canvas size ---- */
  GraphInstance.prototype._computeLayouts = function () {
    var W = this.W, H = this.H;
    if (!W || !H) return;
    var self = this;

    // measure node sizes
    this.nodes.forEach(function (n) {
      var rect = n.el.getBoundingClientRect();
      n.w = rect.width || 96; n.h = rect.height || 34;
    });

    // ----- LEGACY layout: clusters by lc, radial within cluster -----
    var clusters = {};
    this.nodes.forEach(function (n) {
      (clusters[n.def.lc] = clusters[n.def.lc] || []).push(n);
    });
    var keys = Object.keys(clusters);
    var cols = Math.min(keys.length, W < 640 ? 2 : 3);
    var rows = Math.ceil(keys.length / cols);
    keys.forEach(function (k, ci) {
      var col = ci % cols, row = Math.floor(ci / cols);
      var cx = W * (0.5 + 0.28 * Math.cos((ci / keys.length) * Math.PI * 2)) * 0.0 +
        ((col + 0.5) / cols) * W;
      var cy = ((row + 0.5) / rows) * H;
      var members = clusters[k];
      var rad = Math.min(W / cols, H / rows) * 0.30;
      members.forEach(function (n, mi) {
        var a = (mi / members.length) * Math.PI * 2 + ci * 0.7;
        n.lx = cx + Math.cos(a) * rad * (mi === 0 ? 0.18 : 1);
        n.ly = cy + Math.sin(a) * rad * (mi === 0 ? 0.18 : 1);
        // small deterministic jitter
        n.lx += Math.cos(mi * 2.3 + ci) * 8;
        n.ly += Math.sin(mi * 1.7 + ci) * 8;
        n.lx = clamp(n.lx, n.w / 2 + 8, W - n.w / 2 - 8);
        n.ly = clamp(n.ly, n.h / 2 + 8, H - n.h / 2 - 8);
      });
    });

    // ----- DOMAIN layout: requirement nodes in a tidy capability lattice -----
    var dn = this.domainNodes;
    var dCols = W < 640 ? 1 : 2;
    var dRows = Math.ceil(dn.length / dCols);
    dn.forEach(function (d, i) {
      var rect = d.el.getBoundingClientRect();
      d.w = rect.width || 120; d.h = rect.height || 34;
      var col = i % dCols, row = Math.floor(i / dCols);
      d.x = ((col + 0.5) / dCols) * W;
      d.y = ((row + 0.5) / dRows) * H;
      d.x = clamp(d.x, d.w / 2 + 8, W - d.w / 2 - 8);
      d.y = clamp(d.y, d.h / 2 + 8, H - d.h / 2 - 8);
    });
    // each legacy node's domain target = its host requirement position (converge)
    this.nodes.forEach(function (n) {
      var host = self.domainById[n.domainHost];
      if (host) {
        // fan slightly around the host so merges read as a cluster collapse
        var idx = self.model.domain.find(function (d) { return d.id === n.domainHost; });
        n.dx = host.x; n.dy = host.y;
      } else {
        n.dx = n.lx; n.dy = n.ly;
      }
    });
  };

  GraphInstance.prototype._sizeCanvas = function () {
    var field = this.el.field;
    var rect = field.getBoundingClientRect();
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    this.W = rect.width; this.H = rect.height;
    this.dpr = dpr;
    this.el.canvas.width = Math.round(this.W * dpr);
    this.el.canvas.height = Math.round(this.H * dpr);
    this.el.canvas.style.width = this.W + 'px';
    this.el.canvas.style.height = this.H + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._computeLayouts();
  };

  GraphInstance.prototype._onResize = function () {
    this._sizeCanvas();
    // re-anchor placed nodes toward their current-act targets
    var self = this;
    var morphing = this.act === 'rethink';
    this.nodes.forEach(function (n) {
      if (n.placed) {
        n.tx = morphing ? lerp(n.lx, n.dx, self.morph) : n.lx;
        n.ty = morphing ? lerp(n.ly, n.dy, self.morph) : n.ly;
      }
    });
    if (this.reduced) this._renderStaticAct(this.actIndex);
    else this._paint(true);
  };

  /* ---- timer helpers (tracked for teardown) ------------------------------ */
  GraphInstance.prototype._after = function (ms, fn) {
    var id = global.setTimeout(fn, ms);
    this.timers.push(id);
    return id;
  };
  GraphInstance.prototype._clearTimers = function () {
    this.timers.forEach(function (t) { global.clearTimeout(t); });
    this.timers = [];
  };

  /* =========================================================================
     PUBLIC: play / replay / goTo / destroy
     ====================================================================== */
  GraphInstance.prototype.replay = function () { this.play(0); };

  GraphInstance.prototype.goTo = function (i) {
    i = clamp(i | 0, 0, ACTS.length - 1);
    // jump directly to a settled state of act i (scrubbable, non-destructive)
    this._clearTimers();
    this._setActUI(i);
    if (this.reduced) { this._renderStaticAct(i); return; }
    // bring graph to a baseline (all placed, index settled), then apply act state
    this._ensureIndexed();
    this._applyActState(i, true);
    if (!this.raf && this.visible) this._loop();
  };

  GraphInstance.prototype.play = function (startAct) {
    this._clearTimers();
    this._resetRuntime();
    startAct = startAct || 0;
    this._setActUI(startAct);
    if (this.reduced) { this._renderStaticAct(startAct); return; }
    this.running = true;
    this.startTime = now();
    if (this.visible) this._loop();
    if (startAct === 0) this._runIndex();
    else { this._ensureIndexed(); this._applyActState(startAct, true); }
  };

  GraphInstance.prototype.destroy = function () {
    this._clearTimers();
    if (this.raf) global.cancelAnimationFrame(this.raf);
    if (this._tweenRaf) global.cancelAnimationFrame(this._tweenRaf);
    this.raf = null; this._tweenRaf = null; this.running = false;
    if (this._ro) this._ro.disconnect();
    if (this._io) this._io.disconnect();
    if (this._winResize) global.removeEventListener('resize', this._winResize);
    if (this.root) this.root.innerHTML = '';
  };

  /* ---- reset runtime node state ------------------------------------------ */
  GraphInstance.prototype._resetRuntime = function () {
    this.liveEdges = [];
    this.particles = [];
    this.threads = [];
    this.coverProgress = 0;
    this.morph = 0;
    this._placedCount = 0;
    var self = this;
    this.nodes.forEach(function (n) {
      n.placed = false; n.state = 'pending'; n.annotated = false;
      n.vx = n.vy = 0;
      n.x = self.W * 0.5; n.y = self.H * 0.5;
      n.tx = n.lx; n.ty = n.ly;
      n.el.style.opacity = '0';
      n.el.style.transform = 'translate(-50%,-50%) scale(0.7)';
      n.el.dataset.state = 'pending';
      n.el.classList.remove('is-resolved', 'is-risk', 'is-annotated', 'is-focus', 'is-domain-fade');
    });
    this.domainNodes.forEach(function (d) {
      d.vis = 0; d.el.style.opacity = '0';
    });
    this.el.code.querySelectorAll('.alg-srcline').forEach(function (l) { l.classList.remove('hot'); });
    this._updateHud(0, 0, 'INDEX · COBOL→JCL');
  };

  /* mark every node placed + index-settled instantly (for goTo into later acts) */
  GraphInstance.prototype._ensureIndexed = function () {
    var self = this;
    this.liveEdges = this.model.edges.slice();
    this.nodes.forEach(function (n) {
      if (!n.placed) {
        n.placed = true;
        n.x = n.lx; n.y = n.ly; n.tx = n.lx; n.ty = n.ly;
        n.el.style.opacity = '1';
        n.el.style.transform = 'translate(-50%,-50%) scale(1)';
      }
    });
    this._placedCount = this.nodes.length;
    this._updateHud(this.nodes.length, (now() - (this.startTime || now())) / 1000,
      this._modeFor(this.actIndex), this.actIndex === 0 ? ESTATE_NODE_COUNT : null);
  };

  /* =========================================================================
     ACT 1 — INDEX: staggered ignite + particle transit + settle
     ====================================================================== */
  GraphInstance.prototype._runIndex = function () {
    var self = this;
    this.act = 'index'; this.actIndex = 0;
    var order = this.model.nodes;
    var start = 480, step = 210;
    order.forEach(function (def, i) {
      self._after(start + i * step, function () {
        // ignite originating source line
        var line = self.el.code.querySelector('.alg-srcline[data-node="' + def.id + '"]');
        if (line) {
          line.classList.add('hot');
          self._after(560, function () { line.classList.remove('hot'); });
          // scroll source so the hot line stays in view
          var ct = self.el.src;
          var lt = line.offsetTop - ct.clientHeight * 0.5;
          ct.scrollTo ? ct.scrollTo({ top: Math.max(0, lt), behavior: 'smooth' }) : (ct.scrollTop = Math.max(0, lt));
        }
        // launch a particle from the line toward the node slot
        var node = self.nodeById[def.id];
        self._launchParticle(line, node);
      });
    });
    // counters tick
    var ticks = 22;
    for (var k = 1; k <= ticks; k++) {
      (function (kk) {
        self._after(start + kk * (order.length * step) / ticks, function () {
          var nn = Math.round((kk / ticks) * ESTATE_NODE_COUNT);
          self._updateHud(self._placedCount || 0, (now() - self.startTime) / 1000, 'INDEX · COBOL→JCL', nn);
        });
      })(k);
    }
    // when all placed → settle a beat, then auto-advance through acts.
    // Tightened so the whole four-act story lands in ~8-9s: INDEX settles by
    // ~4.5s, then ANNOTATE → COVER → RE-THINK chain through to the cyan payoff.
    var done = start + order.length * step + 500;
    this._after(done, function () {
      self._updateHud(self.nodes.length, (now() - self.startTime) / 1000, 'INDEX · COBOL→JCL', ESTATE_NODE_COUNT);
      if (self.opts.autoChain !== false) {
        self._after(600, function () { self._setActUI(1); self._applyActState(1); });
        self._after(2600, function () { self._setActUI(2); self._applyActState(2); });
        self._after(3700, function () { self._setActUI(3); self._applyActState(3); });
      }
    });
  };

  GraphInstance.prototype._launchParticle = function (lineEl, node) {
    var self = this;
    var fieldRect = this.el.field.getBoundingClientRect();
    var fromX, fromY;
    if (lineEl) {
      var lr = lineEl.getBoundingClientRect();
      fromX = lr.right - fieldRect.left;
      fromY = lr.top + lr.height / 2 - fieldRect.top;
    } else {
      fromX = 0; fromY = this.H * 0.5;
    }
    // if source is offscreen (mobile graph-only), originate from left edge
    if (fromX < 0 || fromX > this.W) { fromX = -10; }
    var toX = node.lx, toY = node.ly;
    var ctrlX = (fromX + toX) / 2;
    var ctrlY = Math.min(fromY, toY) - 46;
    var dur = 560 + Math.random() * 160;
    this.particles.push({
      fromX: fromX, fromY: fromY, toX: toX, toY: toY,
      cx: ctrlX, cy: ctrlY, t0: now(), dur: dur, color: this.tokens.amberGlow,
      onArrive: function () { self._placeNode(node); }
    });
  };

  GraphInstance.prototype._placeNode = function (node) {
    if (node.placed) return;
    node.placed = true;
    node.state = 'pending'; // resolved or risk is decided in act 2 (annotate)
    node.x = node.lx + (Math.random() - 0.5) * 6;
    node.y = node.ly + (Math.random() - 0.5) * 6;
    node.tx = node.lx; node.ty = node.ly;
    node.vx = node.vy = 0;
    node.el.style.opacity = '1';
    node.el.classList.add('is-settling');
    // settle scale animation handled in CSS via class toggle
    node.el.style.transform = 'translate(-50%,-50%) scale(1)';
    this._placedCount = (this._placedCount || 0) + 1;
    // ink in edges whose both endpoints now exist
    var self = this;
    this.model.edges.forEach(function (e) {
      if (self._isLive(e)) return;
      var a = self.nodeById[e.from], b = self.nodeById[e.to];
      if (a && b && a.placed && b.placed) {
        var le = Object.create(e);
        le.t0 = now(); le.inked = 0;
        self.liveEdges.push(le);
      }
    });
    // During ACT 1 the synthetic estate ramp owns the `nodes` readout (the big
    // 10,307 estate count). Don't let the live placed-count clobber it — only
    // keep the elapsed clock ticking. The settled total is written when the
    // build completes (see _runIndex `done`).
    if (this.act === 'index') {
      this._updateHudTime((now() - this.startTime) / 1000);
    } else {
      this._updateHud(this._placedCount, (now() - this.startTime) / 1000, 'INDEX · COBOL→JCL');
    }
  };

  GraphInstance.prototype._isLive = function (e) {
    for (var i = 0; i < this.liveEdges.length; i++) {
      if (this.liveEdges[i].from === e.from && this.liveEdges[i].to === e.to) return true;
    }
    return false;
  };

  /* =========================================================================
     ACT STATE — apply annotate / cover / rethink targets (animated or instant)
     ====================================================================== */
  GraphInstance.prototype._applyActState = function (i, instant) {
    var self = this;
    this.actIndex = i;
    this.act = ACTS[i];
    // INDEX shows the full estate total (the headline); later acts report the
    // on-stage graph count, which is the curated slice the viewer is reading.
    this._updateHud(this.nodes.length, (now() - (this.startTime || now())) / 1000,
      this._modeFor(i), i === 0 ? ESTATE_NODE_COUNT : null);

    if (i >= 1) { this._annotateAll(instant); } else { this._deannotate(); }
    if (i >= 2) { this._coverTo(1, instant); } else { this._coverTo(0, instant); }
    this._morphTo(i >= 3 ? 1 : 0, instant);

    this._emit();
  };

  GraphInstance.prototype._deannotate = function () {
    this.nodes.forEach(function (n) {
      n.annotated = false; n.state = 'pending';
      n.el.classList.remove('is-resolved', 'is-risk', 'is-annotated', 'is-focus');
      n.el.dataset.state = 'pending';
    });
  };

  /* annotate: ring-expansion sweep from a seed, then resolve/risk each node */
  GraphInstance.prototype._annotateAll = function (instant) {
    var self = this;
    var nodes = this.nodes;
    if (instant || this.reduced) {
      nodes.forEach(function (n) { self._resolveNode(n); });
      return;
    }
    // BFS ring order from CBSTM03B
    var order = this._ringOrder('CBSTM03B');
    order.forEach(function (n, idx) {
      self._after(idx * 240, function () {
        n.el.classList.add('is-focus');
        self._after(360, function () { n.el.classList.remove('is-focus'); self._resolveNode(n); });
      });
    });
  };

  GraphInstance.prototype._ringOrder = function (seedId) {
    var self = this;
    var adj = {};
    this.model.edges.forEach(function (e) {
      (adj[e.from] = adj[e.from] || []).push(e.to);
      (adj[e.to] = adj[e.to] || []).push(e.from);
    });
    var seen = {}, q = [seedId], out = [];
    seen[seedId] = true;
    while (q.length) {
      var id = q.shift();
      if (self.nodeById[id]) out.push(self.nodeById[id]);
      (adj[id] || []).forEach(function (nb) {
        if (!seen[nb]) { seen[nb] = true; q.push(nb); }
      });
    }
    // any unreached nodes appended
    this.nodes.forEach(function (n) { if (!seen[n.id]) out.push(n); });
    return out;
  };

  GraphInstance.prototype._resolveNode = function (n) {
    n.annotated = true;
    n.el.classList.add('is-annotated');
    if (n.def.state === 'risk') {
      n.state = 'risk';
      n.el.classList.add('is-risk');
      n.el.dataset.state = 'risk';
    } else {
      n.state = 'resolved';
      n.el.classList.add('is-resolved');
      n.el.dataset.state = 'resolved';
    }
  };

  /* RAF tween helper — drives short value tweens on the animation heartbeat
     (not nested setTimeouts), so they yield to the frame loop and terminate. */
  GraphInstance.prototype._tween = function (dur, ease, onStep, onDone) {
    var self = this, t0 = now();
    function frame() {
      var t = clamp((now() - t0) / dur, 0, 1);
      onStep(ease ? ease(t) : t, t);
      if (t < 1) { self._tweenRaf = global.requestAnimationFrame(frame); }
      else if (onDone) onDone();
    }
    if (this._tweenRaf) global.cancelAnimationFrame(this._tweenRaf);
    this._tweenRaf = global.requestAnimationFrame(frame);
  };

  /* cover: animate the meter; nodes already annotated read as "accounted" */
  GraphInstance.prototype._coverTo = function (target, instant) {
    var self = this;
    if (instant || this.reduced) { this.coverProgress = target; this._updateLedger(); return; }
    var from = this.coverProgress;
    this._tween(900, EASE.outSoft, function (e) {
      self.coverProgress = lerp(from, target, e);
      self._updateLedger();
    });
  };

  GraphInstance.prototype._updateLedger = function () {
    // reflected in HUD when in cover act
    if (this.act === 'cover') {
      var pct = Math.round(this.coverProgress * 100);
      var resolved = this.nodes.filter(function (n) { return n.state === 'resolved'; }).length;
      var flagged = this.nodes.filter(function (n) { return n.state === 'risk'; }).length;
      this._updateHud(this.nodes.length, (now() - (this.startTime || now())) / 1000,
        'ACCOUNTED ' + pct + '% · resolved ' + Math.round(resolved * this.coverProgress) +
        ' · flagged ' + Math.round(flagged * this.coverProgress) + ' · unaccounted 0');
    }
  };

  /* rethink: morph legacy→domain, crossfade colors, reveal requirement nodes */
  GraphInstance.prototype._morphTo = function (target, instant) {
    var self = this;
    if (instant || this.reduced) {
      this.morph = target;
      this._applyMorph();
      this._buildThreads(target > 0.5);
      return;
    }
    var from = this.morph;
    this._buildThreads(target > 0.5);
    this._tween(1400, EASE.inOut, function (e) {
      self.morph = lerp(from, target, e);
      self._applyMorph();
    });
  };

  GraphInstance.prototype._applyMorph = function () {
    var m = this.morph, self = this;
    this.nodes.forEach(function (n) {
      n.tx = lerp(n.lx, n.dx, m);
      n.ty = lerp(n.ly, n.dy, m);
      // legacy nodes fade as they converge into their requirement
      if (n.domainHost) {
        n.el.style.opacity = String(lerp(1, 0.12, m));
        n.el.classList.toggle('is-domain-fade', m > 0.6);
      }
    });
    this.domainNodes.forEach(function (d) {
      d.vis = m;
      d.el.style.opacity = String(clamp((m - 0.35) / 0.65, 0, 1));
      d.el.style.left = d.x + 'px';
      d.el.style.top = d.y + 'px';
      d.el.style.transform = 'translate(-50%,-50%) scale(' + lerp(0.86, 1, clamp((m - 0.35) / 0.65, 0, 1)) + ')';
    });
    // update mode label
    if (this.act === 'rethink') {
      this._updateHud(this.model.domain.length, (now() - (this.startTime || now())) / 1000,
        'RE-THINK · ' + this.model.domain.length + ' requirements · 0 dropped');
    }
  };

  GraphInstance.prototype._buildThreads = function (on) {
    this.threads = [];
    if (!on) return;
    var self = this;
    this.nodes.forEach(function (n) {
      var host = self.domainById[n.domainHost];
      if (host) this.threads.push({ a: n, b: host });
    }, this);
  };

  /* =========================================================================
     RENDER LOOP — canvas paints edges, particles, threads each frame
     ====================================================================== */
  GraphInstance.prototype._loop = function () {
    var self = this;
    this.raf = global.requestAnimationFrame(function () {
      self._tick();
      if (self.running && self.visible && !self.reduced) self._loop();
      else self.raf = null;
    });
  };

  GraphInstance.prototype._tick = function () {
    this._simulate();
    this._paint(false);
  };

  /* force sim: repel + spring + center + damping, only while indexing/settling */
  GraphInstance.prototype._simulate = function () {
    var nodes = this.nodes, n = nodes.length;
    var settling = this.act === 'index';
    for (var i = 0; i < n; i++) {
      var a = nodes[i];
      if (!a.placed) continue;
      var fx = 0, fy = 0;
      if (settling) {
        // repulsion
        for (var j = 0; j < n; j++) {
          if (i === j) continue;
          var b = nodes[j];
          if (!b.placed) continue;
          var dx = a.x - b.x, dy = a.y - b.y;
          var d2 = dx * dx + dy * dy;
          if (d2 < 1) d2 = 1;
          var d = Math.sqrt(d2);
          if (d < 112) {
            var rep = (112 - d) / 112 * 0.9;
            fx += (dx / d) * rep; fy += (dy / d) * rep;
          }
        }
        // springs on live edges
        for (var e = 0; e < this.liveEdges.length; e++) {
          var ed = this.liveEdges[e];
          var other = null;
          if (ed.from === a.id) other = this.nodeById[ed.to];
          else if (ed.to === a.id) other = this.nodeById[ed.from];
          if (other && other.placed) {
            var ex = other.x - a.x, ey = other.y - a.y;
            var elen = Math.sqrt(ex * ex + ey * ey) || 1;
            var spring = (elen - 124) * 0.012;
            fx += (ex / elen) * spring; fy += (ey / elen) * spring;
          }
        }
        // weak pull to layout target (keeps clusters honest)
        fx += (a.tx - a.x) * 0.02;
        fy += (a.ty - a.y) * 0.02;
      } else {
        // non-index acts: ease toward target (annotate stays put, rethink morphs)
        fx += (a.tx - a.x) * 0.14;
        fy += (a.ty - a.y) * 0.14;
      }
      a.vx = (a.vx + fx) * 0.86;
      a.vy = (a.vy + fy) * 0.86;
      a.x += a.vx; a.y += a.vy;
      // clamp into field
      a.x = clamp(a.x, a.w / 2 + 6, this.W - a.w / 2 - 6);
      a.y = clamp(a.y, a.h / 2 + 6, this.H - a.h / 2 - 6);
    }
  };

  GraphInstance.prototype._paint = function (staticPaint) {
    var ctx = this.ctx, T = this.tokens, self = this;
    ctx.clearRect(0, 0, this.W, this.H);
    var tNow = now();

    // ---- traceability threads (act 4, behind edges) ----
    if (this.threads.length && this.morph > 0.4) {
      var alpha = clamp((this.morph - 0.4) / 0.6, 0, 1) * 0.5;
      ctx.lineWidth = 1;
      this.threads.forEach(function (th) {
        var ax = th.a.x, ay = th.a.y, bx = th.b.x, by = th.b.y;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        var mx = (ax + bx) / 2, my = (ay + by) / 2 - 16;
        ctx.quadraticCurveTo(mx, my, bx, by);
        ctx.strokeStyle = 'rgba(125,136,150,' + (alpha * 0.7) + ')';
        ctx.setLineDash([2, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      });
    }

    // ---- edges ----
    var edgeColor = mixHex(FALLBACK.amberCore, FALLBACK.cyanCore, this.morph);
    var edgeFade = 1 - this.morph * 0.75; // legacy edges fade in act4
    for (var i = 0; i < this.liveEdges.length; i++) {
      var ed = this.liveEdges[i];
      var a = this.nodeById[ed.from], b = this.nodeById[ed.to];
      if (!a || !b || !a.placed || !b.placed) continue;
      var ink = ed.t0 ? clamp((tNow - ed.t0) / 480, 0, 1) : 1;
      this._strokeEdge(a, b, ed, ink, edgeColor, edgeFade);
    }
    // domain edges fade in during act4
    if (this.morph > 0.45) {
      var da = clamp((this.morph - 0.45) / 0.55, 0, 1);
      ctx.lineWidth = 1.4;
      this.model.domainEdges.forEach(function (de) {
        var x = self.domainById[de.from], y = self.domainById[de.to];
        if (!x || !y) return;
        ctx.beginPath();
        ctx.moveTo(x.x, x.y);
        var mx = (x.x + y.x) / 2, my = (x.y + y.y) / 2 - 22;
        ctx.quadraticCurveTo(mx, my, y.x, y.y);
        ctx.strokeStyle = 'rgba(61,224,198,' + (da * 0.5) + ')';
        ctx.stroke();
      });
    }

    // ---- particles ----
    for (var p = this.particles.length - 1; p >= 0; p--) {
      var pt = this.particles[p];
      var t = clamp((tNow - pt.t0) / pt.dur, 0, 1);
      var et = EASE.outSoft(t);
      var x = qbez(pt.fromX, pt.cx, pt.toX, et);
      var y = qbez(pt.fromY, pt.cy, pt.toY, et);
      // glow trail
      var grad = ctx.createRadialGradient(x, y, 0, x, y, 7);
      grad.addColorStop(0, T.amberGlow);
      grad.addColorStop(1, 'rgba(201,162,39,0)');
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = T.amberBright;
      ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
      if (t >= 1) {
        if (pt.onArrive) pt.onArrive();
        this.particles.splice(p, 1);
      }
    }

    // ---- position DOM nodes ----
    for (var k = 0; k < this.nodes.length; k++) {
      var nd = this.nodes[k];
      if (!nd.placed) continue;
      nd.el.style.left = nd.x + 'px';
      nd.el.style.top = nd.y + 'px';
      // color crossfade for node dot/border handled via CSS classes + custom prop
      if (this.morph > 0) {
        nd.el.style.setProperty('--morph', this.morph.toFixed(3));
      } else {
        nd.el.style.removeProperty('--morph');
      }
    }
  };

  GraphInstance.prototype._strokeEdge = function (a, b, ed, ink, color, fade) {
    var ctx = this.ctx;
    var ax = a.x, ay = a.y, bx = b.x, by = b.y;
    var mx = (ax + bx) / 2, my = (ay + by) / 2 - 22;
    // ink-in via partial bezier (sample to t=ink)
    ctx.lineWidth = ed.conf ? 0.8 + ed.conf * 1.0 : 1;
    ctx.strokeStyle = fade < 0.99 ? mixHex(FALLBACK.amberCore, FALLBACK.cyanCore, this.morph) : color;
    ctx.globalAlpha = (0.16 + (ed.conf || 0.8) * 0.22) * fade;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    if (ink >= 1) {
      ctx.quadraticCurveTo(mx, my, bx, by);
    } else {
      // draw partial curve
      var steps = 18, last = Math.max(1, Math.floor(steps * ink));
      for (var s = 1; s <= last; s++) {
        var tt = (s / steps);
        var x = qbez(ax, mx, bx, tt), y = qbez(ay, my, by, tt);
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    // arrowhead at t=0.82 once mostly inked
    if (ink > 0.8) {
      var ta = 0.82;
      var hx = qbez(ax, mx, bx, ta), hy = qbez(ay, my, by, ta);
      var hx2 = qbez(ax, mx, bx, ta + 0.02), hy2 = qbez(ay, my, by, ta + 0.02);
      var ang = Math.atan2(hy2 - hy, hx2 - hx);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.globalAlpha = 0.55 * fade;
      ctx.beginPath();
      ctx.moveTo(hx, hy);
      ctx.lineTo(hx - 5 * Math.cos(ang - 0.4), hy - 5 * Math.sin(ang - 0.4));
      ctx.lineTo(hx - 5 * Math.cos(ang + 0.4), hy - 5 * Math.sin(ang + 0.4));
      ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
    }
  };

  function qbez(p0, p1, p2, t) {
    var mt = 1 - t;
    return mt * mt * p0 + 2 * mt * t * p1 + t * t * p2;
  }

  /* =========================================================================
     STATIC (reduced-motion) RENDER — pre-baked settled layouts
     ====================================================================== */
  GraphInstance.prototype._renderStaticAct = function (i) {
    this.actIndex = i; this.act = ACTS[i];
    this._setActUI(i);
    // place all nodes at the right layout instantly, no sim, no particles
    this.liveEdges = this.model.edges.slice();
    this.morph = i >= 3 ? 1 : 0;
    this.coverProgress = i >= 2 ? 1 : 0;
    var self = this;
    this.nodes.forEach(function (n) {
      n.placed = true;
      n.tx = lerp(n.lx, n.dx, self.morph);
      n.ty = lerp(n.ly, n.dy, self.morph);
      n.x = n.tx; n.y = n.ty;
      n.el.style.opacity = (self.morph > 0.6 && n.domainHost) ? '0.12' : '1';
      n.el.style.transform = 'translate(-50%,-50%) scale(1)';
      n.el.style.left = n.x + 'px';
      n.el.style.top = n.y + 'px';
      n.el.classList.remove('is-resolved', 'is-risk', 'is-annotated');
      if (i >= 1) self._resolveNode(n);
      if (self.morph > 0) n.el.style.setProperty('--morph', String(self.morph));
    });
    this._buildThreads(this.morph > 0.5);
    this._applyMorph();
    // single paint
    this._sizeCanvas();
    this._paint(true);
    this._updateHud(
      i >= 3 ? this.model.domain.length : this.nodes.length,
      0, this._modeFor(i), i === 0 ? ESTATE_NODE_COUNT : null
    );
    this._emit();
  };

  /* =========================================================================
     UI sync + HUD + events
     ====================================================================== */
  GraphInstance.prototype._setActUI = function (i) {
    this.actIndex = i; this.act = ACTS[i];
    var btns = this.el.seg.querySelectorAll('.alg-seg-btn');
    for (var k = 0; k < btns.length; k++) {
      var on = (k === i);
      btns[k].classList.toggle('is-active', on);
      btns[k].setAttribute('aria-selected', on ? 'true' : 'false');
    }
    this.root.dataset.act = this.act;
  };

  GraphInstance.prototype._modeFor = function (i) {
    return ['INDEX · COBOL→JCL', 'ANNOTATE · ring-expansion',
      'COVER · accounted 100%', 'RE-THINK · ' + this.model.domain.length + ' requirements'][i];
  };

  GraphInstance.prototype._updateHud = function (nodeCount, secs, mode, bigNodes) {
    var hud = this.el.hud;
    var nn = hud.querySelector('[data-hud="nodes"]');
    var tt = hud.querySelector('[data-hud="time"]');
    var mm = hud.querySelector('[data-hud="mode"]');
    if (nn) nn.textContent = 'nodes ' + (bigNodes != null ? bigNodes.toLocaleString() : nodeCount);
    if (tt) tt.textContent = (secs ? secs.toFixed(1) : '1.8') + 's';
    if (mm) mm.textContent = mode;
  };

  /* Update only the elapsed-time field — used during the index act so node
     placement keeps the clock live without overwriting the estate `nodes` ramp. */
  GraphInstance.prototype._updateHudTime = function (secs) {
    var tt = this.el.hud && this.el.hud.querySelector('[data-hud="time"]');
    if (tt) tt.textContent = (secs ? secs.toFixed(1) : '1.8') + 's';
  };

  GraphInstance.prototype._emit = function () {
    try {
      this.root.dispatchEvent(new CustomEvent('antilegacy:act', {
        detail: { act: this.act, index: this.actIndex }
      }));
    } catch (e) { /* old browsers */ }
  };

  /* =========================================================================
     PUBLIC FACTORY
     ====================================================================== */
  var API = {
    acts: ACTS.slice(),
    mount: function (rootEl, options) {
      if (typeof rootEl === 'string') rootEl = document.querySelector(rootEl);
      if (!rootEl) return null;
      return new GraphInstance(rootEl, options || {});
    },
    /* convenience: auto-mount any [data-alg-graph] elements */
    autoMount: function () {
      var els = document.querySelectorAll('[data-alg-graph]');
      var out = [];
      for (var i = 0; i < els.length; i++) {
        var opts = {};
        if (els[i].hasAttribute('data-alg-static')) opts.autoplay = false;
        if (els[i].hasAttribute('data-alg-no-chain')) opts.autoChain = false;
        if (els[i].hasAttribute('data-alg-act')) opts.startAct = parseInt(els[i].getAttribute('data-alg-act'), 10) || 0;
        var inst = API.mount(els[i], opts);
        if (inst && opts.autoplay === false && opts.startAct) inst.goTo(opts.startAct);
        out.push(inst);
      }
      return out;
    }
  };

  global.AntiLegacyGraph = API;

  // auto-mount on DOM ready (idempotent: skip already-mounted roots)
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  ready(function () {
    var els = document.querySelectorAll('[data-alg-graph]:not([data-alg])');
    if (els.length) API.autoMount();
  });

})(typeof window !== 'undefined' ? window : this);
