/** Missed-call ROI calculator. All maths happens here, in the browser. */
import { currentRegion, type Region } from './region';

interface RegionConfig {
  locale: string;
  currency: string;
  plan: number;
  value: { min: number; max: number; step: number; def: number };
}
interface Preset {
  id: string;
  calls: number;
  hv: number;
  close: number;
  value: Record<Region, number>;
}

export function initRoi() {
  const form = document.getElementById('roi-form') as HTMLFormElement | null;
  if (!form) return;
  const config = JSON.parse(form.dataset.config ?? '{}') as Record<Region, RegionConfig>;
  const presets = JSON.parse(form.dataset.presets ?? '[]') as Preset[];
  const capture = Number(form.dataset.capture) || 0.6;
  const weeks = Number(form.dataset.weeks) || 4.33;
  const presetButtons = Array.from(form.querySelectorAll<HTMLButtonElement>('[data-preset]'));

  const input = (id: string) => form.querySelector<HTMLInputElement>(`#roi-${id}`)!;
  const out = (id: string) => document.getElementById(`roi-${id}-out`)!;
  const el = (id: string) => document.getElementById(id)!;
  const bar = (id: string) => form.querySelector<HTMLElement>(`[data-bar="${id}"]`);
  const barLabel = (id: string) => form.querySelector<HTMLElement>(`[data-bar-label="${id}"]`);

  let region = currentRegion();
  let activePreset: string | null = presetButtons.find((b) => b.getAttribute('aria-pressed') === 'true')?.dataset.preset ?? null;
  const money = (n: number) =>
    new Intl.NumberFormat(config[region].locale, { style: 'currency', currency: config[region].currency, maximumFractionDigits: 0 }).format(n);

  const setFill = (i: HTMLInputElement) => {
    const min = Number(i.min);
    const max = Number(i.max);
    i.style.setProperty('--fill', `${(((Number(i.value) - min) / (max - min)) * 100).toFixed(1)}%`);
  };

  const label = (id: string, text: string) => {
    out(id).textContent = text;
    input(id).setAttribute('aria-valuetext', text);
  };

  const markPreset = (id: string | null) => {
    activePreset = id;
    presetButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.preset === id)));
  };

  const update = () => {
    const calls = Number(input('calls').value);
    const hv = Number(input('hv').value) / 100;
    const value = Number(input('value').value);
    const close = Number(input('close').value) / 100;

    label('calls', String(calls));
    label('hv', `${Math.round(hv * 100)}%`);
    label('value', money(value));
    label('close', `${Math.round(close * 100)}%`);
    ['calls', 'hv', 'value', 'close'].forEach((id) => setFill(input(id)));

    const leak = calls * weeks * hv * close * value;
    const recovered = leak * capture;
    const plan = config[region].plan;
    const multiple = recovered / plan;
    const months = Math.floor(value / plan);
    const width = (n: number) => `${Math.max(1.5, Math.min(100, leak ? (n / leak) * 100 : 0)).toFixed(1)}%`;

    el('roi-leak').textContent = money(Math.round(leak));
    el('roi-recovered').textContent = money(Math.round(recovered));
    el('roi-multiple').textContent = multiple >= 1 ? `${Math.round(multiple)}×` : `${multiple.toFixed(1)}×`;
    el('roi-payback').textContent =
      months >= 1
        ? `One accepted case covers ${months} ${months === 1 ? 'month' : 'months'} of the plan.`
        : 'One accepted case covers most of a month of the plan.';
    const r = bar('recovered');
    const p = bar('plan');
    if (r) r.style.width = width(recovered);
    if (p) p.style.width = width(plan);
    const lL = barLabel('leak');
    const lR = barLabel('recovered');
    const lP = barLabel('plan');
    if (lL) lL.textContent = money(Math.round(leak));
    if (lR) lR.textContent = money(Math.round(recovered));
    if (lP) lP.textContent = money(plan);
  };

  const applyRegion = (r: Region) => {
    region = r;
    const v = config[r].value;
    const i = input('value');
    i.min = String(v.min);
    i.max = String(v.max);
    i.step = String(v.step);
    const preset = presets.find((p) => p.id === activePreset);
    i.value = String(preset ? preset.value[r] : v.def);
    update();
  };

  const applyPreset = (id: string) => {
    const p = presets.find((x) => x.id === id);
    if (!p) return;
    input('calls').value = String(p.calls);
    input('hv').value = String(p.hv);
    input('close').value = String(p.close);
    input('value').value = String(p.value[region]);
    markPreset(id);
    update();
    announce();
  };

  const announce = () => {
    el('roi-summary').textContent =
      `About ${el('roi-leak').textContent} a month leaking to voicemail; about ${el('roi-recovered').textContent} recovered, ` +
      `${el('roi-multiple').textContent} the plan price.`;
  };

  presetButtons.forEach((b) => b.addEventListener('click', () => applyPreset(b.dataset.preset ?? '')));
  form.addEventListener('submit', (e) => e.preventDefault());
  form.addEventListener('input', (e) => {
    if ((e.target as HTMLElement).matches('input[type="range"]')) markPreset(null);
    update();
  });
  form.addEventListener('change', announce);
  document.addEventListener('regionchange', (e) => {
    applyRegion((e as CustomEvent<Region>).detail);
    announce();
  });
  // region-init.js may have switched the page before this ran.
  if (form.dataset.pageRegion !== region) applyRegion(region);
  else update();
}
