import {
  ButtonProps,
  Button as CunninghamButton,
} from '@gouvfr-lasuite/cunningham-react';

/**
 * This component is a wrapper around the Cunningham Button component.
 *
 * When using Next we must use <Link/> to perform navigation without refreshing the page, so when an
 * href is provided this component wraps the Cunningham Button with a <Link/> component.
 *
 * @param props
 * @constructor
 */
export const Button = (props: ButtonProps) => {
  return <CunninghamButton {...props} />;
};
