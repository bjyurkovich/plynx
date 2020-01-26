import React from 'react';
import PropTypes from 'prop-types';


export function makeControlButton(props) {
  return (
    <div
       onClick={(e) => {
         e.preventDefault();
         if (props.enabled !== false) {
            props.func();
         }
       }}
       key={props.key}
       className={["control-button", (props.className || ''), (props.selected ? "selected" : ""), (props.enabled !== false ? 'enabled' : 'disabled')].join(" ")}
    >
       <img src={"/icons/" + props.img} alt={props.text}/>
       <div className='control-button-text'>{props.text}</div>
    </div>
  );
}

// TODO make a single function makeControlButton and makeControlLink
export function makeControlLink(props) {
  return (
    <a
       href={props.href}
       key={props.key}
       className={["control-button", (props.className || ''), (props.selected ? "selected" : ""), (props.enabled !== false ? 'enabled' : 'disabled')].join(" ")}
    >
       <img src={"/icons/" + props.img} alt={props.text}/>
       <div className='control-button-text'>{props.text}</div>
    </a>
  );
}

export function makeControlToggles(props) {
    return (
        <div
            className='control-toggle'
            key={props.key}
        >
            {props.items.map(
                (item, index) => {
                    if (index === props.index) {
                        item["selected"] = true;
                    }
                    item["key"] = index;
                    item.func = () => {
                        props.func(item.value);
                        props.onIndexChange(index);
                    }
                    if (index === 0) {
                        item['className'] = 'first';
                    }
                    if (index === props.items.length - 1) {
                        item['className'] = 'last';
                    }

                    return makeControlButton(item);
                }
            )}
        </div>
    );
}

makeControlButton.propTypes = {
  func: PropTypes.func.isRequired,
  className: PropTypes.string,
  img: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  selected: PropTypes.bool,
  key: PropTypes.number,
};
