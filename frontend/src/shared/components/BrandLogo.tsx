type BrandLogoVariant = 'on-blue' | 'on-dark' | 'on-light';

interface BrandLogoProps {
  variant: BrandLogoVariant;
  className?: string;
}

const LOGO_SOURCE: Record<BrandLogoVariant, string> = {
  'on-blue': '/brand/logo-on-blue.png',
  'on-dark': '/brand/logo-on-dark.png',
  'on-light': '/brand/logo-on-light.png',
};

export function BrandLogo({ variant, className = '' }: BrandLogoProps) {
  return (
    <span className={`brand-logo brand-logo--${variant} ${className}`.trim()}>
      <img src={LOGO_SOURCE[variant]} alt="中国五矿、中冶集团与宝冶钢构" />
    </span>
  );
}
