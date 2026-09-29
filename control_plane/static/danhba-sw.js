var BUILD = "__DANHBA_BUILD__";
var CACHE = "danhba-" + BUILD;

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys
          .filter(function (key) {
            return key.indexOf("danhba-") === 0 && key !== CACHE;
          })
          .map(function (key) {
            return caches.delete(key);
          })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/danhba") !== 0) return;
  if (url.pathname.indexOf(".mobileconfig") !== -1) return;
  event.respondWith(
    fetch(event.request, { cache: "no-store" })
      .then(function (fresh) {
        var copy = fresh.clone();
        caches.open(CACHE).then(function (cache) {
          cache.put(event.request, copy);
        });
        return fresh;
      })
      .catch(function () {
        return caches.match(event.request);
      })
  );
});
