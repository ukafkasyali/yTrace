import type { RequirementDefinition, RequirementPriority } from '../services';

export type RequirementChoice = RequirementPriority | 'DISABLED';
export type RequirementPriorities = Record<string, RequirementChoice>;

export function selectedRequirements(
  requirements: RequirementDefinition[],
  priorities: RequirementPriorities,
) {
  return requirements.flatMap(requirement => {
    const priority = requirement.isSystemRequired ? 'MUST' : priorities[requirement.id];
    return priority && priority !== 'DISABLED' ? [{ ...requirement, priority }] : [];
  });
}

export default function RequirementEditor({
  requirements,
  priorities,
  onPriorityChange,
}: {
  requirements: RequirementDefinition[];
  priorities: RequirementPriorities;
  onPriorityChange: (id: string, priority: RequirementChoice) => void;
}) {
  return <section className="requirement-editor" aria-labelledby="research-contract-title">
    <div>
      <p className="eyebrow">Preflight</p>
      <h3 id="research-contract-title">Research contract</h3>
      <p>Mandatory requirements reject candidates. Preferred requirements guide ranking only.</p>
    </div>
    <div className="requirement-list">{requirements.map(requirement =>
      <div className="requirement-row" key={requirement.id}>
        <div>
          <strong>{requirement.label}</strong>
          <span>{requirement.category === 'OTHER' ? 'Custom' : requirement.category.replaceAll('_', ' ').toLowerCase()}</span>
          <small>{requirement.description}</small>
        </div>
        {requirement.isSystemRequired
          ? <span className="requirement-fixed">Always required</span>
          : <label>
            <span>Priority for {requirement.label}</span>
            <select
              value={priorities[requirement.id] ?? requirement.priority}
              onChange={event => onPriorityChange(requirement.id, event.target.value as RequirementChoice)}
            >
              <option value="MUST">Must have</option>
              <option value="SHOULD">Preferred</option>
              <option value="DISABLED">Disabled</option>
            </select>
          </label>}
      </div>,
    )}</div>
  </section>;
}
