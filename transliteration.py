import json
from pathlib import Path

# Load transliteration mapping at startup
def load_transliteration_map(json_path):
    json_path = 'gondi_proper.json'
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        mapping = {
            'Vowels': {},
            'Consonants': {},
            'VowelSigns': {},
            'Modifiers': {}
        }
        for category in mapping.keys():
            for key in data.get(category, {}):
                # Directly map Devanagari character to Gondi script
                mapping[category][key] = data[category][key]["gondi_script"]
        return mapping
    

def devanagari_to_gondi(devanagari_str, mapping):
    devanagari_str = devanagari_str.strip()
    result = []
    i = 0
    n = len(devanagari_str)
    
    while i < n:
        base_consonant = None
        
        # Handle standalone vowels first
        if devanagari_str[i] in mapping['Vowels']:
            result.append(mapping['Vowels'][devanagari_str[i]])
            i += 1
        # Check for consonant + virama + consonant (conjuncts)
        elif i+2 < n and devanagari_str[i] in mapping['Consonants'] and devanagari_str[i+1] == '्' and devanagari_str[i+2] in mapping['Consonants']:
            # Combine the consonants without the virama
            result.append(mapping['Consonants'][devanagari_str[i]])
            result.append(mapping['Consonants'][devanagari_str[i+2]])
            # Set base_consonant to the second consonant for attaching vowel signs
            base_consonant = devanagari_str[i+2]
            i += 3
        # Check for consonant + virama (half consonant)
        elif i+1 < n and devanagari_str[i] in mapping['Consonants'] and devanagari_str[i+1] == '्':
            # Handle half consonant by appending the consonant without the virama
            result.append(mapping['Consonants'][devanagari_str[i]])
            # Set base_consonant for vowel signs
            base_consonant = devanagari_str[i]
            i += 2
        # Check for consonant + vowel sign
        elif i+1 < n and devanagari_str[i] in mapping['Consonants'] and devanagari_str[i+1] in mapping['VowelSigns']:
            # Append consonant and vowel sign
            result.append(mapping['Consonants'][devanagari_str[i]])
            result.append(mapping['VowelSigns'][devanagari_str[i+1]])
            base_consonant = devanagari_str[i]
            i += 2
        # Check for standalone consonant
        elif devanagari_str[i] in mapping['Consonants']:
            result.append(mapping['Consonants'][devanagari_str[i]])
            base_consonant = devanagari_str[i]
            i += 1
        else:
            result.append(devanagari_str[i])
            i += 1
        
        # Attach vowel signs to the base consonant
        if base_consonant is not None and i < n and devanagari_str[i] in mapping['VowelSigns']:
            result.append(mapping['VowelSigns'][devanagari_str[i]])
            i += 1
    return ''.join(result)