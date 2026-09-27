// Atlante : cette adresse est fermée.
// Ce service worker remplace l'ancien : il efface la copie hors ligne, recharge les pages ouvertes
// (qui affichent alors l'avis de fermeture) et se désinstalle. Aucune requête n'est interceptée.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) {
  e.waitUntil((async function () {
    try { const ks = await caches.keys(); await Promise.all(ks.map(function (k) { return caches.delete(k); })); } catch (err) {}
    try { await self.clients.claim(); } catch (err) {}
    try {
      const cs = await self.clients.matchAll({ type: 'window' });
      await Promise.all(cs.map(function (c) { return c.navigate(c.url).catch(function () {}); }));
    } catch (err) {}
    try { await self.registration.unregister(); } catch (err) {}
  })());
});
