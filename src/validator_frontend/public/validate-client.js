async function submitValidation(approved, hasModifications) {
  const form = document.getElementById('validationForm');
  const formData = new FormData(form);
  const modifications = formData.get('modifications');
  const shouldHaveModifications = hasModifications && modifications;
  const payload = {
    imageId: parseInt(formData.get('imageId')),
    approved: approved,
    modifications: shouldHaveModifications ? modifications : null
  };

  try {
    const response = await fetch('/validate/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      location.reload();
    } else {
      alert('Eroare la trimitere. Te rog să încerci din nou.');
    }
  } catch (err) {
    alert('Eroare de conexiune.');
  }
}
