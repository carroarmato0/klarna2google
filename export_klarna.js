// Export your Klarna loyalty cards to klarna_cards.json.
//
// Log in at https://app.klarna.com, open the browser console (F12 → Console)
// and paste this whole file. Everything runs in your own browser: the card
// list goes from Klarna straight into a file in your Downloads folder.
(async () => {
  // Klarna keeps the market (BE, NL, …) and the session token in localStorage.
  const market = (localStorage.getItem('safe-storage/v1/@klapp-traits/analytics-client/user-attributes/market') || 'BE')
    .split(';;').pop().toLowerCase();
  let token = localStorage.getItem('@KLAPP:signIn:accessToken');
  if (!token) throw new Error('Not logged in: log in at app.klarna.com first.');
  try {
    const parsed = JSON.parse(token);
    token = typeof parsed === 'string' ? parsed : (parsed.token || parsed.accessToken || parsed.value);
  } catch {}

  const response = await fetch(`/${market}/api/loyalty_cards_bff/v1/loyalty-content`, {
    headers: {Authorization: `Bearer ${token}`},
  });
  if (!response.ok) throw new Error(`Klarna answered HTTP ${response.status}; reload the page and try again.`);

  // id.raw is the number printed on the card; content is what the barcode encodes.
  // They differ for some cards (e.g. an extra prefix in the barcode).
  const cards = (await response.json()).loyalty_identifiers.map(c => {
    const barcode = c.processed.barcode;
    const card = {name: c.processed.name, number: barcode.id.raw || barcode.content, barcode_format: barcode.format};
    if (barcode.content !== card.number) card.barcode_value = barcode.content;
    return card;
  });

  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(cards, null, 2) + '\n'], {type: 'application/json'}));
  link.download = 'klarna_cards.json';
  link.click();
  console.log(`${cards.length} cards saved to klarna_cards.json`);
})();
