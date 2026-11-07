const form = document.querySelector('#promotion-form');
const status = document.querySelector('#form-status');
form?.addEventListener('submit', (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const name = new FormData(form).get('listing_name');
  status.textContent = `Thanks — your request for ${name} is ready for review. We’ll follow up by email.`;
  form.reset();
});
