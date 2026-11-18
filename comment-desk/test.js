"use strict";

const assert = require("assert");
const lib = require("./scanlib");

const posts = Array.from({ length: 10 }, (_, i) => ({ id: String(i) }));
assert.strictEqual(lib.leftoverDoors([], posts, 0), 0);
assert.strictEqual(lib.leftoverDoors([{ status: "Live", skipUntil: 0 }], [], 0), 0);
assert.strictEqual(
  lib.leftoverDoors(
    Array.from({ length: 20 }, () => ({ status: "Live", skipUntil: 0 })),
    posts,
    0,
  ),
  8,
);
assert.strictEqual(lib.leftoverDoors([{ status: "Die", skipUntil: 0 }], posts, 0), 0);
assert.strictEqual(lib.homeDoors([], 1000), 0);
assert.strictEqual(lib.homeDoors([{ seenAt: 1000, homeBurned: true }], 1000), 0);
assert.strictEqual(lib.homeDoors([{ seenAt: 1000, homeBurned: false }], 1000), 16);

const parked = Array.from({ length: 12 }, (_, i) => ({ status: "im", skipUntil: 1000 + i }));
assert.strictEqual(lib.releasePark(parked, 500), 10);
assert.strictEqual(parked.filter((p) => p.skipUntil > 500).length, 10);

const shell = lib.parseGuest("<html>login login login login</html>", 200);
assert.strictEqual(shell.kind, "im");
const hit = lib.parseGuest('"legacy_fbid":"99","name":"An","text":"xin gia"', 200);
assert.strictEqual(hit.kind, "ok");
assert.strictEqual(hit.comments[0].text, "xin gia");
assert.strictEqual(lib.parseGuest("ok", 429).kind, "429");
assert.strictEqual(lib.explainSeat("ok", { added: 2, old: 0 }).viec, "mới 2");
assert.strictEqual(lib.explainSeat("ok", { added: 0, old: 4 }).viec, "cũ 4");
assert.strictEqual(lib.leanPosts(Array.from({ length: 800 }, (_, i) => i)).length, 500);

const proxy = { failTimes: [], status: "" };
lib.noteFail(proxy, 1000);
assert.strictEqual(proxy.status, "fetch failed");
lib.noteFail(proxy, 2000);
assert.strictEqual(proxy.status, "Die");

assert.strictEqual(lib.parseCount('"comment_count":{"total_count":12}'), 12);
assert.strictEqual(lib.decideRadar(3, 3, "ok", false).action, "hold");
assert.strictEqual(lib.decideRadar(3, 4, "counts_shell", false).kind, "counts_shell");
assert.strictEqual(lib.decideRadar(3, 4, "ok", true).action, "save");
assert.strictEqual(lib.decideRadar(null, null, "timeout", false).kind, "timeout");
const many = Array.from({ length: 20 }, (_, i) => ({ id: `p${i}` }));
const hot = lib.hotPosts(many, ["p5", "p6"], ["p9"]);
assert.deepStrictEqual(hot.map((post) => post.id), ["p9", "p5", "p6", "p0", "p1", "p2", "p3", "p4"]);

const windowed = Array.from({ length: 200 }, (_, i) => ({ id: `p${i}` }));
assert.strictEqual(lib.pcSlice(windowed, 1).length, 32);
assert.strictEqual(lib.pcSlice(windowed, 1)[0].id, "p0");
assert.strictEqual(lib.pcSlice(windowed, 4)[0].id, "p96");
assert.strictEqual(lib.pcSlice(windowed, 5).length, 0);
assert.strictEqual(lib.windowPosts(windowed).length, 128);

console.log("scanlib ok");
