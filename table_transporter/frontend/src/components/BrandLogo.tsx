// Yapı Kredi Teknoloji logosu (public/brand/ykt-logo.webp, şeffaf arka plan, 512x165).
export function BrandLogo({ height = 28 }: { height?: number }) {
  return <img className="brand-logo" src="/brand/ykt-logo.webp" alt="Yapı Kredi Teknoloji" height={height} width={Math.round((height * 512) / 165)} />
}
