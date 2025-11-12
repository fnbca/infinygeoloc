import streamlit as st
import os
import base64
import requests
from PIL import Image, ImageOps
import folium
from streamlit_folium import st_folium # Import correct
from streamlit_js_eval import get_geolocation
from dotenv import load_dotenv
import sys

# --- Configuration API (chargée une seule fois) ---
load_dotenv()
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
ACCOUNT_KEY = os.getenv("ACCOUNT_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ==============================================================================
# SECTION 1 : FONCTIONS DE L'APPLICATION (FIDEALIS, COLLAGES)
# ==============================================================================

def api_login():
    """
    Se connecte à l'API Fidealis en utilisant les clés d'environnement.
    """
    try:
        login_response = requests.get(
            f"{API_URL}?key={API_KEY}&call=loginUserFromAccountKey&accountKey={ACCOUNT_KEY}"
        )
        login_response.raise_for_status() # Vérifie les erreurs HTTP
        login_data = login_response.json()
        if 'PHPSESSID' in login_data:
            return login_data["PHPSESSID"]
    except requests.exceptions.RequestException as e:
        st.error(f"Erreur de connexion API Fidealis: {e}")
    return None

def api_upload_files(description, files, session_id):
    """
    Envoie les fichiers (collages) à l'API Fidealis par lots de 12.
    """
    for i in range(0, len(files), 12):
        batch_files = files[i:i + 12]
        data = {
            "key": API_KEY,
            "PHPSESSID": session_id,
            "call": "setDeposit",
            "description": description,
            "type": "deposit",
            "hidden": "0",
            "sendmail": "1",
        }
        files_to_send = {}
        try:
            for idx, file_path in enumerate(batch_files, start=1):
                with open(file_path, "rb") as f:
                    encoded_file = base64.b64encode(f.read()).decode("utf-8")
                    data[f"filename{idx}"] = os.path.basename(file_path)
                    data[f"file{idx}"] = encoded_file
            
            response = requests.post(API_URL, data=data)
            response.raise_for_status()
        except IOError as e:
            st.error(f"Erreur lors de la lecture du fichier {file_path}: {e}")
        except requests.exceptions.RequestException as e:
            st.error(f"Erreur lors de l'envoi du lot {i//12 + 1}: {e}")

def create_collage(images, output_path, max_images=3):
    """
    Crée un collage horizontal à partir d'une liste d'images PIL.
    """
    min_height = min(img.size[1] for img in images)
    resized_images = [ImageOps.fit(img, (int(img.size[0] * min_height / img.size[1]), min_height)) for img in images]
    total_width = sum(img.size[0] for img in resized_images) + (len(resized_images) - 1) * 20 + 50
    collage = Image.new("RGB", (total_width, min_height + 50), (255, 255, 255))
    x_offset = 25
    for img in resized_images:
        collage.paste(img, (x_offset, 25))
        x_offset += img.size[0] + 20
    collage.save(output_path)

def create_all_collages(files, client_name):
    """
    Prend une liste de chemins de fichiers image et crée des collages par groupe de 3.
    """
    collages = []
    for i in range(0, len(files), 3):
        group = files[i:i + 3]
        try:
            images = [Image.open(f) for f in group]
            collage_name = f"c_{client_name.replace(' ', '_')}_{len(collages) + 1}.jpg"
            create_collage(images, collage_name, max_images=len(group))
            collages.append(collage_name)
        except Exception as e:
            st.error(f"Erreur lors de la création du collage pour le groupe {i//3}: {e}")
    return collages

def get_quantity_for_product_4(credit_data):
    """
    Extrait la quantité du produit '4' des données de crédit.
    """
    if isinstance(credit_data, dict) and "4" in credit_data:
        return credit_data["4"]["quantity"]
    return "N/A"

def get_credit(session_id):
    """
    Récupère les données de crédit Fidealis pour l'utilisateur.
    """
    credit_url = f"{API_URL}?key={API_KEY}&PHPSESSID={session_id}&call=getCredits&product_ID="
    try:
        response = requests.get(credit_url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Erreur de récupération des crédits: {e}")
    return None


# ==============================================================================
# SECTION 2 : COMPOSANT DE GÉOLOCALISATION
# ==============================================================================

def get_coords_from_address_text(address, gmaps_api_key):
    """
    Appelle l'API Google Geocoding pour obtenir les coords d'une adresse texte.
    """
    if not gmaps_api_key:
        st.error("❌ Clé API Google (GMAPS_API_KEY) absente.")
        return None, None
    
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(address)}&key={gmaps_api_key}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data['status'] == 'OK':
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
    except requests.exceptions.RequestException as e:
        st.error(f"Erreur de géocodage: {e}")
    return None, None

def get_address_from_coords(lat, lon, gmaps_api_key):
    """
    Appelle l'API Google Geocoding et renvoie l'adresse formatée.
    """
    if not gmaps_api_key:
        st.error("❌ Clé API Google (GMAPS_API_KEY) absente.")
        return None
    
    url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lon}&key={gmaps_api_key}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]["formatted_address"]
        else:
            st.error(f"⚠️ API Google n'a pas renvoyé de résultat. Statut: {data.get('status')}")
            return "Adresse introuvable"
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Erreur lors de l'appel à l'API Google : {e}")
        return "Erreur API"

def render_geolocation_component(key_prefix):
    """
    Affiche le composant de géolocalisation.
    Met à jour st.session_state avec les clés :
    - f"{key_prefix}_address"
    - f"{key_prefix}_lat"
    - f"{key_prefix}_lon"
    """
    
    # --- 1. Initialisation de l'état interne ---
    coords_key = f"{key_prefix}_coords" # Coords internes pour la carte
    address_key = f"{key_prefix}_address" # Clé publique pour l'adresse
    lat_key = f"{key_prefix}_lat"       # Clé publique pour la latitude
    lon_key = f"{key_prefix}_lon"       # Clé publique pour la longitude
    triggered_key = f"{key_prefix}_triggered"
    
    if coords_key not in st.session_state:
        st.session_state[coords_key] = None
        st.session_state[triggered_key] = False
        
    # --- 2. Disposition ---
    st.subheader("Aide à la géolocalisation")
    st.write("Utilisez le bouton ou la carte pour remplir automatiquement les champs.")
    col1, col2 = st.columns([1, 1])

    with col1:
        if st.button("📍 Géolocalisez-moi (Position actuelle)"):
            st.session_state[triggered_key] = True
            st.info("⏳ Récupération de la localisation...")

        if st.session_state[triggered_key]:
            location = get_geolocation()
            
            if location is None:
                st.info("⏳ En attente... veuillez autoriser la géolocalisation.")
            else:
                st.session_state[triggered_key] = False 
                
                if location.get("PERMISSION_DENIED"):
                    st.error("❌ Permission refusée.")
                elif location.get("POSITION_UNAVAILABLE"):
                    st.error("❌ Position GPS indisponible.")
                elif isinstance(location, dict) and "coords" in location:
                    lat = location["coords"].get("latitude")
                    lon = location["coords"].get("longitude")
                    if lat and lon:
                        st.session_state[coords_key] = {"lat": lat, "lon": lon}
                        addr = get_address_from_coords(lat, lon, GOOGLE_API_KEY)
                        
                        # MISE À JOUR DES CLÉS DU FORMULAIRE
                        st.session_state[address_key] = addr
                        # CORRECTION TypeError: Convertir les floats en string
                        st.session_state[lat_key] = str(lat)
                        st.session_state[lon_key] = str(lon)
                        
                        st.rerun()
                else:
                    st.error("🚨 Format inattendu de get_geolocation().")

        # Affichage de l'adresse (si trouvée)
        if st.session_state.get(address_key): # Utilise .get() pour plus de sécurité
            st.info(f"**🏠 Adresse :** {st.session_state[address_key]}")

    # --- 3. Affichage de la carte ---
    with col2:
        st.write("Si la position n'est pas exacte, cliquez sur la carte.")

        DEFAULT_CENTER = [48.8566, 2.3522]
        
        # Le centre de la carte est basé sur les coords internes, ou les coords du formulaire, ou Paris
        map_center = DEFAULT_CENTER
        has_coords = False
        if st.session_state[coords_key]:
             map_center = [st.session_state[coords_key]["lat"], st.session_state[coords_key]["lon"]]
             has_coords = True
        elif st.session_state.get(lat_key) and st.session_state.get(lon_key):
             try:
                 map_center = [float(st.session_state[lat_key]), float(st.session_state[lon_key])]
                 has_coords = True
             except (ValueError, TypeError):
                 pass # Garde le centre par défaut si lat/lon ne sont pas valides

        m = folium.Map(
            location=map_center, 
            zoom_start=17 if has_coords else 12, # Zoom si coords, sinon large
            tiles='Esri WorldImagery', name='Satellite'
        )

        # Le marqueur est basé sur les coords internes OU les coords du formulaire
        if has_coords:
            tooltip = st.session_state.get(address_key, "Position")
            folium.Marker(map_center, tooltip=tooltip, popup=tooltip).add_to(m)

        folium.LayerControl().add_to(m)
        
        # Correction AttributeError: st_folium (underscore)
        map_data = st_folium(m, center=map_center, width=None, height=400, key="geoloc_map_main")

        # --- 4. Gérer le clic ---
        if map_data and map_data.get("last_clicked"):
            new_lat = map_data["last_clicked"]["lat"]
            new_lon = map_data["last_clicked"]["lng"]
            
            # Vérifie si le clic est nouveau
            clicked_coords_are_new = True
            if st.session_state[coords_key]:
                if (abs(st.session_state[coords_key]["lat"] - new_lat) < 1e-7 and \
                    abs(st.session_state[coords_key]["lon"] - new_lon) < 1e-7):
                    clicked_coords_are_new = False

            if clicked_coords_are_new:
                st.session_state[coords_key] = {"lat": new_lat, "lon": new_lon}
                addr = get_address_from_coords(new_lat, new_lon, GOOGLE_API_KEY)
                
                # MISE À JOUR DES CLÉS DU FORMULAIRE
                st.session_state[address_key] = addr
                # CORRECTION TypeError: Convertir les floats en string
                st.session_state[lat_key] = str(new_lat)
                st.session_state[lon_key] = str(new_lon)
                
                st.rerun()

# ==============================================================================
# SECTION 3 : APPLICATION STREAMLIT PRINCIPALE
# ==============================================================================

st.title("Formulaire de dépôt FIDEALIS pour INFINY")

session_id = api_login()
if session_id:
    credit_data = get_credit(session_id)
    if isinstance(credit_data, dict):
        product_4_quantity = get_quantity_for_product_4(credit_data)
        st.write(f"Crédit restant : {product_4_quantity}")
    else:
        st.error("Échec de la récupération des données de crédit.")
else:
    st.error("Échec de la connexion Fidealis.")
    st.stop() 

# --- Début du Formulaire ---
client_name = st.text_input("Nom du client")

# --- INTÉGRATION DU COMPOSANT GÉO ---

# 1. Initialiser les clés du formulaire
if "form_address" not in st.session_state:
    st.session_state.form_address = ""
if "form_lat" not in st.session_state:
    st.session_state.form_lat = ""
if "form_lon" not in st.session_state:
    st.session_state.form_lon = ""

# CORRECTION StreamlitAPIException:
# On appelle le composant (qui modifie le state) AVANT de dessiner les widgets
st.divider()
render_geolocation_component(key_prefix="form")
st.divider()

# 2. Lier les champs (l'utilisateur peut taper à tout moment)
address = st.text_input("Adresse complète ", key="form_address")

# 3. Bouton pour synchroniser l'adresse texte -> coords
if st.button("actualiser la carte ⬆️"):
    if st.session_state.form_address:
        lat, lon = get_coords_from_address_text(st.session_state.form_address, GOOGLE_API_KEY)
        if lat is not None and lon is not None:
            # CORRECTION TypeError: Convertir les floats en string
            st.session_state.form_lat = str(lat)
            st.session_state.form_lon = str(lon)
            
            # On met aussi à jour la clé interne de la carte
            st.session_state.form_coords = {"lat": lat, "lon": lon}
            
            st.success("Coordonnées trouvées et mises à jour.")
            st.rerun() # Pour rafraîchir la carte
        else:
            st.error("Impossible de trouver les coordonnées pour cette adresse.")
    else:
        st.warning("Veuillez d'abord saisir une adresse.")

latitude = st.text_input("Latitude ", key="form_lat")
longitude = st.text_input("Longitude ", key="form_lon")

# L'appel au composant a été déplacé plus haut

# --- Suite du Formulaire ---
uploaded_files = st.file_uploader("Téléchargez les photos (JPEG/PNG)", accept_multiple_files=True, type=["jpg", "png"])

if st.button("Soumettre"):
    # On lit les valeurs finales depuis st.session_state
    addr = st.session_state.form_address
    lat = st.session_state.form_lat
    lon = st.session_state.form_lon

    if not client_name or not addr or not lat or not lon or not uploaded_files:
        st.error("Veuillez remplir tous les champs  et télécharger au moins une photo.")
    else:
        st.info("Préparation de l'envoi...")
        
        saved_files = []
        temp_dir = f"temp_{client_name.replace(' ', '_')}"
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
            
        for idx, file in enumerate(uploaded_files):
            save_path = os.path.join(temp_dir, f"{client_name.replace(' ', '_')}_temp{idx + 1}.jpg")
            with open(save_path, "wb") as f:
                f.write(file.read())
            saved_files.append(save_path)

        st.info("Création des collages...")
        collages = create_all_collages(saved_files, client_name)

        if collages:
            first_collage = collages[0]
            renamed_first_collage = os.path.join(os.path.dirname(first_collage), f"{client_name.replace(' ', '_')}_1.jpg")
            if os.path.exists(renamed_first_collage):
                 os.remove(renamed_first_collage)
            try:
                os.rename(first_collage, renamed_first_collage)
                collages[0] = renamed_first_collage
            except Exception as e:
                st.warning(f"Erreur lors du renommage du fichier: {e}")

        description = f"SCELLÉ NUMERIQUE Bénéficiaire: Nom: {client_name}, Adresse: {addr}, Coordonnées GPS: Latitude {lat}, Longitude {lon}"

        st.info("Envoi des données...")
        api_upload_files(description, collages, session_id)
        st.success("Données envoyées avec succès !")
        
        try:
            for f in saved_files:
                os.remove(f)
            for c in collages:
                os.remove(c)
            os.rmdir(temp_dir)
        except OSError as e:
            st.warning(f"Erreur lors du nettoyage des fichiers temporaires: {e}")
