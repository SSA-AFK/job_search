import { Search, X } from "lucide-react";

import type { CompanyScale, CompanySearchParams, FundingStage } from "../api/types";
import type { SearchParamKey } from "./search-params";

type FiltersProps = {
  params: CompanySearchParams;
  searchValue: string;
  hasActiveFilters: boolean;
  onSearchChange: (value: string) => void;
  onFilterChange: (key: SearchParamKey, value: string) => void;
  onClear: () => void;
};

const options = {
  industry: [
    ["Artificial Intelligence", "人工智能"],
    ["Internet Technology", "互联网科技"],
  ],
  sub_industry: [
    ["Foundation Models", "基础模型"],
    ["Multimodal Models", "多模态模型"],
    ["AI Platforms", "AI 平台"],
  ],
  funding_stage: [
    ["seed", "种子轮"],
    ["angel", "天使轮"],
    ["pre_a", "Pre-A 轮"],
    ["series_a", "A 轮"],
    ["series_b", "B 轮"],
    ["series_c_plus", "C 轮及以后"],
    ["public", "已上市"],
    ["unfunded", "未融资"],
    ["unknown", "未知"],
  ] satisfies ReadonlyArray<readonly [FundingStage, string]>,
  scale: [
    ["one_to_49", "1-49 人"],
    ["50_to_199", "50-199 人"],
    ["200_to_499", "200-499 人"],
    ["500_plus", "500 人以上"],
    ["unknown", "未知"],
  ] satisfies ReadonlyArray<readonly [CompanyScale, string]>,
  city: [
    ["Beijing", "北京"],
    ["Shanghai", "上海"],
    ["Hangzhou", "杭州"],
    ["Shenzhen", "深圳"],
  ],
} as const;

function SelectFilter({
  label,
  name,
  value,
  choices,
  onChange,
}: {
  label: string;
  name: SearchParamKey;
  value?: string;
  choices: ReadonlyArray<readonly [string, string]>;
  onChange: FiltersProps["onFilterChange"];
}) {
  const hasUnknownValue = Boolean(value && !choices.some(([optionValue]) => optionValue === value));

  return (
    <label className="filter-control">
      <span>{label}</span>
      <select value={value ?? ""} onChange={(event) => onChange(name, event.target.value)}>
        <option value="">全部</option>
        {hasUnknownValue ? <option value={value}>{value}</option> : null}
        {choices.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Filters({
  params,
  searchValue,
  hasActiveFilters,
  onSearchChange,
  onFilterChange,
  onClear,
}: FiltersProps) {
  return (
    <section className="filters" aria-label="公司筛选">
      <label className="filter-control search-control">
        <span>搜索公司</span>
        <span className="search-input-wrap">
          <Search aria-hidden="true" size={18} strokeWidth={1.8} />
          <input
            type="search"
            value={searchValue}
            placeholder="公司名称或别名"
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </span>
      </label>
      <SelectFilter label="行业" name="industry" value={params.industry} choices={options.industry} onChange={onFilterChange} />
      <SelectFilter label="细分领域" name="sub_industry" value={params.sub_industry} choices={options.sub_industry} onChange={onFilterChange} />
      <SelectFilter label="融资阶段" name="funding_stage" value={params.funding_stage} choices={options.funding_stage} onChange={onFilterChange} />
      <SelectFilter label="公司规模" name="scale" value={params.scale} choices={options.scale} onChange={onFilterChange} />
      <SelectFilter label="城市" name="city" value={params.city} choices={options.city} onChange={onFilterChange} />
      <button className="clear-filters" type="button" onClick={onClear} disabled={!hasActiveFilters}>
        <X aria-hidden="true" size={16} />
        清除全部筛选
      </button>
    </section>
  );
}
