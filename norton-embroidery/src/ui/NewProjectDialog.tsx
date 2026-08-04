import { useState } from 'react';
import { defaultHoop, getHoop, machineProfiles } from '../domain/machine';
import { mmToUnits, unitsToMm } from '../domain/units';
import type { NewProjectInput } from '../app/pipeline';

export function NewProjectDialog(props: {
  onCreate: (input: NewProjectInput) => void;
  onCancel: () => void;
  canCancel: boolean;
}): React.JSX.Element {
  const [name, setName] = useState('Untitled design');
  const [customer, setCustomer] = useState('');
  const [machineId, setMachineId] = useState(machineProfiles[0].id);
  const machine = machineProfiles.find((m) => m.id === machineId)!;
  const [hoopId, setHoopId] = useState(defaultHoop(machine).id);
  const hoop = getHoop(machine, hoopId) ?? defaultHoop(machine);
  const [widthMm, setWidthMm] = useState(90);
  const [heightMm, setHeightMm] = useState(90);

  const tooWide = mmToUnits(widthMm) > hoop.width;
  const tooTall = mmToUnits(heightMm) > hoop.height;

  const selectMachine = (id: string): void => {
    setMachineId(id);
    const m = machineProfiles.find((p) => p.id === id)!;
    const h = defaultHoop(m);
    setHoopId(h.id);
    setWidthMm(Math.min(widthMm, unitsToMm(h.width)));
    setHeightMm(Math.min(heightMm, unitsToMm(h.height)));
  };

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>New embroidery project</h2>
        <div className="body">
          <div className="field">
            <label htmlFor="np-name">Project name</label>
            <input id="np-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="np-customer">Customer (optional)</label>
            <input id="np-customer" value={customer} onChange={(e) => setCustomer(e.target.value)} />
          </div>
          <div className="row">
            <div className="field">
              <label htmlFor="np-machine">Machine</label>
              <select id="np-machine" value={machineId} onChange={(e) => selectMachine(e.target.value)}>
                {machineProfiles.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="np-hoop">Hoop</label>
              <select id="np-hoop" value={hoopId} onChange={(e) => setHoopId(e.target.value)}>
                {machine.hoops.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="row">
            <div className="field">
              <label htmlFor="np-width">Design width (mm)</label>
              <input
                id="np-width"
                type="number"
                min={5}
                max={unitsToMm(hoop.width)}
                value={widthMm}
                onChange={(e) => setWidthMm(Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label htmlFor="np-height">Design height (mm)</label>
              <input
                id="np-height"
                type="number"
                min={5}
                max={unitsToMm(hoop.height)}
                value={heightMm}
                onChange={(e) => setHeightMm(Number(e.target.value))}
              />
            </div>
          </div>

          {tooWide || tooTall ? (
            <div className="error-banner">
              {tooWide
                ? `Width ${widthMm} mm exceeds the ${hoop.name} field of ${unitsToMm(hoop.width).toFixed(0)} mm. `
                : ''}
              {tooTall
                ? `Height ${heightMm} mm exceeds the ${hoop.name} field of ${unitsToMm(hoop.height).toFixed(0)} mm.`
                : ''}
            </div>
          ) : null}

          <p className="note">
            The defaults suit most logos — you can accept them and change the size later once you can see the
            stitches.
          </p>
          <p className="note">{machine.notes}</p>
        </div>
        <div className="footer">
          {props.canCancel ? <button onClick={props.onCancel}>Cancel</button> : null}
          <button
            className="primary"
            disabled={!name.trim() || tooWide || tooTall}
            onClick={() =>
              props.onCreate({
                name: name.trim(),
                customer: customer.trim() || undefined,
                machineId,
                hoopId,
                width: mmToUnits(widthMm),
                height: mmToUnits(heightMm),
              })
            }
          >
            Create project
          </button>
        </div>
      </div>
    </div>
  );
}
