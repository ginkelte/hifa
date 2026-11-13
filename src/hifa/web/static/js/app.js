/* hifa dashboard client (SPEC-070 §2.6). No build step, no eval: CSP script-src 'self'. */
(function () {
  "use strict";

  document.body.classList.remove("no-js");
  document.body.classList.add("js");

  var PANEL = "#panel";

  function toast(message, isError) {
    var stack = document.getElementById("toast-stack");
    if (!stack) return;
    var node = document.createElement("div");
    node.className = isError ? "toast is-error" : "toast";
    node.textContent = message;
    stack.appendChild(node);
    window.setTimeout(function () { node.remove(); }, 4000);
  }

  function panelIsOpen() {
    var panel = document.querySelector(PANEL);
    return panel !== null && panel.children.length > 0;
  }

  function closePanel() {
    var panel = document.querySelector(PANEL);
    if (panel) panel.innerHTML = "";
    // Whatever a foreign write left pending applies the moment the panel closes.
    flushRevision();
  }

  // --- the revision of the page ---------------------------------------------------------
  // Only an answer that replaced the collection moves the number; a panel or a row did not
  // update the board, so believing their number would leave the board old with a quiet poller.
  var revision = {
    known: document.body.dataset.revision || "",
    pending: null,
    inFlight: false,
    watchdog: null
  };

  // htmx has no request timeout: a reload still in flight after this is abandoned and asked again.
  var RELOAD_MS = 10000;
  var reloads = 0;

  function isProbe(elt) {
    return !!elt && elt.id === "revision-probe";
  }

  function refreshTarget() {
    if (document.getElementById("board")) return "#board";
    if (document.getElementById("issues-tbody")) return "#issues-tbody";
    return "";
  }

  // The two nodes the poller swaps, and the only two whose answer the page may believe it is
  // current about.
  function isCollectionTarget(node) {
    if (!node) return false;
    return node.id === "board" || node.id === "issues-tbody";
  }

  function quickAddHoldsText() {
    var input = document.querySelector("#quick-add .quick-add-input");
    return input !== null && input.value.trim() !== "";
  }

  // A swap deletes what the element holds, so it waits: an open panel, unsaved text, a drag.
  function blocked() {
    return panelIsOpen() || quickAddHoldsText() || document.body.dataset.dragging === "true";
  }

  // The revision the collection now shows, so a pending value is spent by adopting it.
  function adopt(number) {
    if (number === null || number === undefined || number === "") return;
    revision.known = String(number);
    revision.pending = null;
  }

  function flushRevision() {
    if (revision.pending === null) return;
    var target = refreshTarget();
    var fragment = document.body.dataset.refresh || "";
    if (!target || !fragment) {
      adopt(revision.pending);
      return;
    }
    // The pending value is deliberately not spent here: it survives the deferral and only goes
    // away when the collection really reloaded (or when the answer adopted its number), so a swap
    // that never lands cannot pass for one that did.
    if (blocked()) return;
    if (revision.inFlight) return;
    swapInto(target, withLiveFilters(fragment));
  }

  // The reload asks the question the page is showing, not the one its URL was built from: the
  // filter bar holds the values the reader typed and the page's query predates them.
  function withLiveFilters(url) {
    var bar = document.querySelector("form.filter-bar");
    if (!bar) return url;
    var cut = url.indexOf("?");
    var params = new URLSearchParams(cut < 0 ? "" : url.slice(cut + 1));
    new FormData(bar).forEach(function (value, key) {
      var text = String(value).trim();
      if (text === "") params.delete(key);
      else params.set(key, text);
    });
    return (cut < 0 ? url : url.slice(0, cut)) + "?" + params.toString();
  }

  // An htmx request, never a plain fetch: the fragment carries hx-* on its cards and only htmx
  // binds them. `outerHTML` because the fragment carries the id of the node it replaces.
  function swapInto(target, url) {
    reloads += 1;
    var token = reloads;
    revision.inFlight = true;
    window.clearTimeout(revision.watchdog);
    revision.watchdog = window.setTimeout(function () {
      if (token !== reloads || !revision.inFlight) return;
      // Give up on the request that never answered and ask again: one hung reload must not
      // silence the page for the rest of the session.
      revision.inFlight = false;
      revision.watchdog = null;
      flushRevision();
    }, RELOAD_MS);
    var done = function () {
      settle(token);
    };
    window.htmx.ajax("GET", url, { target: target, swap: "outerHTML" }).then(done, done);
  }

  // The request is back. An answer that replaced the collection already adopted its number and
  // spent the pending one with it; one that failed adopts nothing, so the pending value stays and
  // the next tick asks again — once per interval, not in a loop on the same URL.
  function settle(token) {
    if (token !== reloads) return; // an abandoned request answering late is not the current one
    revision.inFlight = false;
    window.clearTimeout(revision.watchdog);
    revision.watchdog = null;
  }

  function onProbe(xhr) {
    if (xhr.status !== 200) return;
    var number = null;
    try {
      number = JSON.parse(xhr.responseText).revision;
    } catch (error) {
      return;
    }
    if (typeof number !== "number") return;
    if (String(number) === revision.known) {
      revision.pending = null;
      return;
    }
    revision.pending = String(number);
    flushRevision();
  }

  // htmx compiles a `[...]` trigger filter with `Function`, which `script-src 'self'` forbids:
  // the visibility condition runs here, where cancelling the request is a normal event.
  document.body.addEventListener("htmx:beforeRequest", function (event) {
    if (!isProbe(event.detail.elt)) return;
    if (document.hidden) event.preventDefault();
  });

  // Adopt only an answer that replaced the collection; the others did not update the board.
  document.body.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail.xhr;
    if (!xhr) return;
    if (isProbe(event.detail.elt)) {
      onProbe(xhr);
      return;
    }
    if (xhr.status < 200 || xhr.status > 299) return;
    if (!isCollectionTarget(event.detail.target)) return;
    adopt(xhr.getResponseHeader("HX-Revision"));
    flushRevision();
  });

  // htmx does not swap 4xx: enable the swap for 422 so the invalid field comes back (R11).
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr && event.detail.xhr.status === 422) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  // json encoding for PATCH (replaces the json-enc extension: 6 lines, no extra vendor).
  document.body.addEventListener("htmx:configRequest", function (event) {
    if (event.detail.verb === "patch") {
      event.detail.headers["Content-Type"] = "application/json";
      event.detail.body = JSON.stringify(event.detail.parameters);
    }
  });

  document.body.addEventListener("htmx:responseError", function (event) {
    var status = event.detail.xhr.status;
    if (status === 409 || status === 500) toast("could not save: the server rejected the change", true);
  });

  document.body.addEventListener("issue-created", function () { toast("issue created"); });
  document.body.addEventListener("issue-updated", function () { toast("issue updated"); });
  document.body.addEventListener("issue-moved", function () { toast("issue moved"); });

  // focus survives a swap (2.6.2): remember the identifier, restore it afterwards.
  document.body.addEventListener("htmx:beforeSwap", function () {
    var focused = document.activeElement;
    document.body.dataset.focusIdentifier = focused && focused.dataset ? focused.dataset.identifier || "" : "";
  });
  document.body.addEventListener("htmx:afterSwap", function () {
    var identifier = document.body.dataset.focusIdentifier;
    if (!identifier) return;
    var node = document.querySelector('[data-identifier="' + identifier + '"]');
    if (node) node.focus();
  });

  function focusables() {
    return Array.prototype.slice.call(document.querySelectorAll("tr.row, article.card"));
  }

  function step(delta) {
    var nodes = focusables();
    if (!nodes.length) return;
    var current = document.activeElement;
    var index = nodes.indexOf(current);
    var next = index < 0 ? 0 : index + delta;
    if (next < 0) next = 0;
    if (next > nodes.length - 1) next = nodes.length - 1;
    nodes[next].focus();
    if (nodes[next].scrollIntoView) nodes[next].scrollIntoView({ block: "nearest" });
  }

  function isEditable(target) {
    if (!target || !target.tagName) return false;
    var tag = target.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable === true;
  }

  document.addEventListener("keydown", function (event) {
    var editable = isEditable(event.target);

    if (event.key === "Escape") {
      if (panelIsOpen()) closePanel();
      return;
    }
    if (editable) return;
    if (event.metaKey || event.ctrlKey) return;

    if (event.key === "j") { event.preventDefault(); step(1); }
    else if (event.key === "k") { event.preventDefault(); step(-1); }
    else if (event.key === "/") {
      var input = document.querySelector("input[name=q]");
      if (input) { event.preventDefault(); input.focus(); input.select(); }
    } else if (event.key === "Enter") {
      var focused = document.activeElement;
      if (focused && focused.getAttribute && focused.getAttribute("data-href")) {
        window.location.assign(focused.getAttribute("data-href"));
      }
    }
  });

  // A finger that moved is a scroll: the click the browser leaves behind is swallowed first.
  var tap = { x: 0, y: 0, moved: false };
  var TAP_SLOP = 10;
  document.addEventListener("touchstart", function (event) {
    if (event.touches.length !== 1) { tap.moved = true; return; }
    tap.x = event.touches[0].clientX;
    tap.y = event.touches[0].clientY;
    tap.moved = false;
  }, { passive: true });
  document.addEventListener("touchmove", function (event) {
    if (event.touches.length !== 1) { tap.moved = true; return; }
    if (Math.abs(event.touches[0].clientX - tap.x) > TAP_SLOP ||
        Math.abs(event.touches[0].clientY - tap.y) > TAP_SLOP) {
      tap.moved = true;
    }
  }, { passive: true });
  document.addEventListener("click", function (event) {
    if (!tap.moved) return;
    if (!event.target.closest) return;
    if (!event.target.closest(".card, .row, [data-href]")) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  // opening a panel by clicking a row keeps the URL in the history (hx-push-url).
  document.body.addEventListener("click", function (event) {
    var closer = event.target.closest ? event.target.closest("[data-close-panel]") : null;
    if (closer) {
      event.preventDefault();
      closePanel();
    }
  });

  // a successful quick add answers with HX-Trigger: issue-created; empty the box again.
  document.body.addEventListener("issue-created", function () {
    var form = document.getElementById("quick-add");
    if (form) form.reset();
    flushRevision();
  });

  // An emptied quick add ends the interaction that was holding the swap back.
  document.body.addEventListener("input", function (event) {
    var target = event.target;
    if (!target || !target.classList || !target.classList.contains("quick-add-input")) return;
    if (String(target.value || "").trim() === "") flushRevision();
  });

  // board columns: the whole header strip drags and a drag that starts on a card belongs to
  // the card instance (filter). The drop sends the ULIDs of the neighbours to
  // POST /_/states/{id}/position and, when the write fails, the DOM goes back to the order the
  // server still has (SPEC-070 §2.6.5, R25).
  var columnPositionUrl = function (stateId) {
    return "/_/states/" + encodeURIComponent(stateId) + "/position";
  };

  // `onStart` captures the two nodes that surrounded the column before Sortable moved it; the
  // index alone is not enough, because by `onEnd` it already points past the removed node.
  function revertColumn(board, item, origin) {
    if (!origin) return;
    var reference = origin.previous ? origin.previous.nextElementSibling : board.firstElementChild;
    if (reference === item) return;
    board.insertBefore(item, reference);
  }

  // SPEC-070 §2.6.5: two ids and nothing else — `before` is the column on the left and `after`
  // the one on the right, `null` at an edge. The server computes the float.
  function columnPayload(item) {
    var previous = item.previousElementSibling;
    var next = item.nextElementSibling;
    return {
      before: previous ? previous.getAttribute("data-state-id") : null,
      after: next ? next.getAttribute("data-state-id") : null
    };
  }

  function persistColumn(board, item, origin) {
    var stateId = item.getAttribute("data-state-id");
    if (!stateId) {
      document.body.dataset.dragging = "false";
      flushRevision();
      return;
    }
    window
      .fetch(columnPositionUrl(stateId), {
        method: "POST",
        headers: { "HX-Request": "true", "Content-Type": "application/json" },
        body: JSON.stringify(columnPayload(item))
      })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
      })
      .catch(function () {
        revertColumn(board, item, origin);
        toast("could not move the column", true);
      })
      .then(function () {
        // The drop's own write has returned, so the revision the drag was holding back applies
        // now — and not one moment earlier: the board swap is what this flag was blocking.
        document.body.dataset.dragging = "false";
        flushRevision();
      });
  }

  function initBoardSortable() {
    var board = document.getElementById("board");
    if (!board || typeof window.Sortable === "undefined") return;
    // R18: a read-only board offers no drag at all — the write would only answer 403.
    if (board.getAttribute("data-can-write") !== "true") return;
    if (board.getAttribute("data-sortable") === "on") return;
    board.setAttribute("data-sortable", "on");
    var origin = null;
    window.Sortable.create(board, {
      animation: 120,
      draggable: ".column",
      filter: ".card",
      // A card is not a drag target, so the browser keeps the gesture: with the default
      // (`preventOnFilter: true`) Sortable cancels the `touchstart` of every touch that lands on a
      // card, and a phone can no longer scroll from there.
      preventOnFilter: false,
      ghostClass: "column-ghost",
      // A finger that swipes must scroll. Dragging a column starts only after a long press, and
      // only once the finger has moved past a few pixels, so the gesture is never ambiguous.
      delay: 220,
      delayOnTouchOnly: true,
      touchStartThreshold: 6,
      onStart: function (event) {
        // A swap during a drag would move the node out from under the pointer.
        document.body.dataset.dragging = "true";
        // The neighbour, not the index: by `onEnd` the index past the removed node is wrong.
        origin = { previous: event.item.previousElementSibling };
      },
      onEnd: function (event) {
        // A drop that changed nothing ends the interaction here; a real one keeps the flag until
        // its own POST returns, so the deferred board swap cannot fire in the middle of it.
        if (event.oldIndex === event.newIndex) {
          document.body.dataset.dragging = "false";
          flushRevision();
          return;
        }
        persistColumn(board, event.item, origin);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBoardSortable);
  } else {
    initBoardSortable();
  }

  // htmx swaps the whole board on every filter change: re-attach the handler to the new node.
  document.body.addEventListener("htmx:afterSwap", initBoardSortable);
})();
