import { ManaColors } from './DeckVisuals';

const COLORS = [
  {id:'W',name:'White'}, {id:'U',name:'Blue'}, {id:'B',name:'Black'},
  {id:'R',name:'Red'}, {id:'G',name:'Green'},
];

export default function ColorIdentityFilter({value,onChange,disabled=false}: {
  value:string;onChange:(value:string)=>void;disabled?:boolean;
}) {
  function toggle(color:string) {
    const current=value==='C'?'':value;
    const next=current.includes(color)?current.replace(color,''):current+color;
    onChange(COLORS.filter(item=>next.includes(item.id)).map(item=>item.id).join(''));
  }
  return <fieldset className="research-wide color-identity-filter" disabled={disabled}>
    <legend>Within color identity</legend>
    <div className="identity-modes">
      <button type="button" disabled={disabled} aria-pressed={value===''} onClick={()=>onChange('')}>Any colors</button>
      <button type="button" disabled={disabled} aria-pressed={value==='C'} onClick={()=>onChange('C')}><span aria-hidden="true"><ManaColors colors={['C']}/></span>Colorless only</button>
    </div>
    <div className="identity-colors">
      {COLORS.map(color=><button type="button" disabled={disabled} key={color.id} aria-pressed={value.includes(color.id)} onClick={()=>toggle(color.id)}>
        <span aria-hidden="true"><ManaColors colors={[color.id]}/></span>{color.name}
      </button>)}
    </div>
    <p>{value==='C'?'Only cards with a colorless identity.':value?'Cards may use any subset of the selected colors. Colorless cards are included.':'No color restriction. Choose any combination above, or Colorless only.'}</p>
  </fieldset>;
}
