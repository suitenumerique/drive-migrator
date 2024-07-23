import { VariantType } from '@openfun/cunningham-react';
import type { Meta, StoryObj } from '@storybook/react';

import { Badge } from '@/components/Badge/Badge';

const meta = {
  title: 'Badge',
  component: Badge,
  // This component will have an automatically generated Autodocs entry: https://storybook.js.org/docs/writing-docs/autodocs
  tags: ['autodocs'],

  args: {
    children: 'Badge',
  },
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Info: Story = {
  args: {
    variant: VariantType.INFO,
  },
};

export const Success: Story = {
  args: {
    variant: VariantType.SUCCESS,
  },
};

export const Error: Story = {
  args: {
    variant: VariantType.ERROR,
  },
};

export const Warning: Story = {
  args: {
    variant: VariantType.WARNING,
  },
};
