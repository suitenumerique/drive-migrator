import {
  ButtonProps,
  Button as CunninghamButton,
} from '@openfun/cunningham-react';
import Link from 'next/link';

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
  if (props.href) {
    // By setting href="#" we make sure Cunningham renders the button as an anchor tag making passHref work.
    return (
      <Link href={props.href} passHref legacyBehavior>
        <CunninghamButton {...props} href="#" />
      </Link>
    );
  }
  return <CunninghamButton {...props} />;
};
