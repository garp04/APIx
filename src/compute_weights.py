import glob
import json
import re
import pandas as pd

# 1. Identify all DGCA Excel sheets in data/raw
files = sorted(glob.glob('data/raw/DOM CITYPAIR DATA*.xlsx'))
print(f"Found {len(files)} DGCA monthly files to aggregate.")

# Target representative high-traffic sectors
TARGET_BASKET = [
    ('DELHI', 'MUMBAI'),
    ('DELHI', 'BANGALORE'),
    ('MUMBAI', 'BANGALORE'),
    ('DELHI', 'KOLKATA'),
    ('BANGALORE', 'HYDERABAD'),
    ('CHENNAI', 'DELHI'),
    ('MUMBAI', 'GOA'),
    ('DELHI', 'AHMEDABAD'),
    ('DELHI', 'HYDERABAD'),
    ('DELHI', 'PATNA')
]

# Aliases to normalize common variations in DGCA city spellings
CITY_ALIASES = {
    'BENGALURU': 'BANGALORE',
    'CALCUTTA': 'KOLKATA',
    'MADRAS': 'CHENNAI',
    'BOMBAY': 'MUMBAI',
    'NEW DELHI': 'DELHI'
}

def clean_city(name):
    if not isinstance(name, str):
        return ""
    name = re.sub(r'[\r\n\t]+', ' ', name).strip().upper()
    return CITY_ALIASES.get(name, name)

# 2. Iterate through all files and accumulate total passengers
sector_passengers = {}

for f in files:
    try:
        df = pd.read_excel(f, header=2)
        # Normalize column names by removing extra spaces and newlines
        col_map = {c: re.sub(r'[\r\n\t]+', ' ', str(c)).strip() for c in df.columns}
        df = df.rename(columns=col_map)
        
        c1 = [c for c in df.columns if 'CITY 1' in c][0]
        c2 = [c for c in df.columns if 'CITY 2' in c][0]
        pax1 = [c for c in df.columns if 'PASSENGERS' in c and 'TO CITY 2' in c][0]
        pax2 = [c for c in df.columns if 'PASSENGERS' in c and 'FROM CITY 2' in c][0]

        df[c1] = df[c1].apply(clean_city)
        df[c2] = df[c2].apply(clean_city)
        df[pax1] = pd.to_numeric(df[pax1], errors='coerce').fillna(0)
        df[pax2] = pd.to_numeric(df[pax2], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            city_a, city_b = row[c1], row[c2]
            # Symmetrize the sector (DEL-BOM is the same market as BOM-DEL)
            sorted_pair = tuple(sorted([city_a, city_b]))
            total_pax = row[pax1] + row[pax2]

            sector_passengers[sorted_pair] = sector_passengers.get(sorted_pair, 0) + total_pax
    except Exception as e:
        print(f"Error parsing {f}: {e}")

# 3. Filter for our representative basket and calculate weights
basket_data = []
for orig, dest in TARGET_BASKET:
    pair_key = tuple(sorted([clean_city(orig), clean_city(dest)]))
    pax = sector_passengers.get(pair_key, 0)
    basket_data.append({
        'sector': f"{orig}-{dest}",
        'city1': orig,
        'city2': dest,
        'total_passengers': int(pax)
    })

df_basket = pd.DataFrame(basket_data)
total_basket_pax = df_basket['total_passengers'].sum()
df_basket['weight'] = df_basket['total_passengers'] / total_basket_pax

# 4. Save results to data/processed
df_basket.to_csv('data/processed/sector_weights.csv', index=False)

weights_dict = dict(zip(df_basket['sector'], df_basket['weight']))
with open('data/processed/sector_weights.json', 'w') as jf:
    json.dump(weights_dict, jf, indent=4)

print("\n--- Consolidated Sector Weights (w_i) ---")
print(df_basket[['sector', 'total_passengers', 'weight']].to_string(index=False))
print("\nSaved to data/processed/sector_weights.csv and sector_weights.json")