# The soundscape

Every kind of place someone on the line can be in, and every sound that happens there. Generated from [`wayne/audio/soundscape.json`](../wayne/audio/soundscape.json) by `scripts/soundscape.py --doc`; how it plays is in [`wayne/audio/ambience.py`](../wayne/audio/ambience.py).

Each place is a loop (made once, kept). Some lay another loop under theirs (a crowd under a busy street), and the weather goes over anywhere outdoors, or on the windows of anywhere with them. Sounds happen at random at about the rate shown, from the distances shown, and whoever's there hears them as they happen. A place with bursts and rests (a training hall) goes quiet between them; a place that can cut into someone's sentences (a fight, a heavy bag) says so.

102 places, 170 sounds.

## Places

| Place | What it is | What they'd say they can hear | Sounds there (every ~seconds) |
|---|---|---|---|
| `street_day` | A busy city street by day: traffic passing, footsteps, car horns in the distance. *(over chatter; out in the weather)* | traffic, people walking past | horn (35s), car_pass (25s), bus (120s), siren (150s), motorbike (160s), truck_pass (90s), laugh_group (140s), shout (220s), pigeons (300s), bike_bell (240s), heels (180s), manhole (220s), brakes (400s), alarm (420s), train (260s), horn_truck (300s), ambulance (500s), vendor (300s), car_door (160s), skateboard (400s), dog (300s) |
| `street_rush` | Rush hour gridlock on a big city avenue: idling engines, buses, a wall of commuters' footsteps and chatter, horns everywhere. *(over chatter; out in the weather)* | rush-hour traffic, horns, crowds | horn (15s), whistle (90s), bus (80s), horn_truck (120s), shout (120s), siren (140s), brakes (240s), motorbike (150s), car_door (120s), laugh_group (160s), bike_bell (200s), ambulance (400s) |
| `street_night` | A city street at night: sparse traffic hiss, a distant siren, a dog barking far off. *(out in the weather)* | a few cars, sirens somewhere | siren (75s), car_pass (45s), dog (120s), alarm (300s), train (240s), gunshots (600s), scream (900s), laugh_group (240s), steam (300s), manhole (260s), motorbike (200s), cat_yowl (400s), helicopter (420s), horn (150s), ambulance (500s), shout (400s), bottle_kick (500s) |
| `alley` | A narrow alley at night: water dripping from fire escapes, steam hissing from a vent, traffic muffled beyond the buildings. *(out in the weather)* | dripping, steam, the street muffled beyond | drip (40s), steam (90s), dumpster (180s), bottle_kick (240s), fire_escape (200s), cat_yowl (300s), siren (200s), rat (200s), gunshots (700s), shout (400s) |
| `plaza` | A busy city plaza: a crowd milling and chatting, a fountain splashing, traffic around the edges. *(out in the weather)* | a crowd, a fountain, pigeons | pigeons (60s), laugh_group (90s), vendor (120s), horn (120s), siren (240s), church_bells (900s), bike_bell (200s), skateboard (240s) |
| `market` | An open-air street market: vendors calling out indistinctly, crates set down, bags rustling, a crowd haggling. *(out in the weather)* | the stalls, people haggling | vendor (40s), rustle (60s), laugh_group (120s), horn (150s), pigeons (200s), till (150s) |
| `construction` | A construction site beside a city street: an excavator rumbling, metal clanging, a generator droning, traffic going past. *(out in the weather)* | the building site, machinery | jackhammer (40s), reverse_beep (60s), hammer (50s), horn (120s), truck_pass (90s), whistle (200s) |
| `bridge` | On a long suspension bridge: traffic rushing past close by, wind gusting, the deck humming, water far below. *(out in the weather)* | traffic rushing past on the bridge, the wind | truck_pass (30s), horn (60s), motorbike (90s), wind_gust (45s), siren (180s), ferry (300s), foghorn (400s) |
| `docks` | A harbour at night: water lapping at the pilings, a foghorn far off, gulls, rope creaking. *(out in the weather)* | water against the pilings, gulls, a foghorn | foghorn (130s), gulls (70s), door (200s), reverse_beep (260s), ferry (300s), alarm (600s), hull (150s) |
| `waterfront` | A riverside promenade: water slapping the embankment, joggers' footsteps, gulls, traffic on the road behind. *(out in the weather)* | the river, gulls, joggers going by | gulls (60s), ferry (200s), bike_bell (150s), dog (180s), laugh_group (200s), helicopter (400s), skateboard (300s) |
| `dog_walk` | Walking a dog along a riverside path: the dog's tags jingling, its paws, water lapping, gulls. *(out in the weather)* | the dog's tags, the water, gulls | dog_tags (40s), gulls (70s), dog (150s), bike_bell (200s), laugh_group (240s) |
| `park` | A city park by day: birdsong, leaves rustling in a light breeze, children playing in the distance. *(out in the weather)* | birds, kids playing somewhere, the city past the trees | birds (40s), dog (120s), bike_bell (180s), laugh_group (200s), siren (300s), plane (400s) |
| `park_night` | A big city park at night: wind in the trees, an owl, traffic far beyond the trees. *(over crickets; out in the weather)* | wind in the trees, traffic far off | owl (150s), siren (200s), dog (240s) |
| `garden` | A garden: birdsong, a light breeze through hedges, a distant lawnmower. *(out in the weather)* | birds, a breeze, a mower somewhere | birds (35s), shears (45s), watering_can (70s), hose (110s), trowel (120s), wheelbarrow (240s), plane (400s), church_bells (1200s) |
| `greenhouse` | Inside a glass greenhouse: a mister hissing softly, water dripping off leaves, birds muffled outside the glass. *(rain on the windows)* | the mister hissing, water dripping, birds outside | drip (50s), watering_can (60s), shears (80s), birds (90s), tap (200s) |
| `cemetery` | A cemetery at night: wind through old trees, an iron gate creaking, the city far away. *(over crickets; out in the weather)* | wind, a gate creaking, the city a long way off | owl (120s), wind_gust (90s), church_bells (900s), dog (300s) |
| `pier` | An old seaside amusement pier: rides rattling and clanking, distant screams of fun on a coaster, game booth bells, waves under the boards. *(out in the weather)* | rides rattling, people screaming on the coaster, the sea under the boards | gulls (80s), laugh_group (90s), cheer (200s) |
| `pier_night` | A derelict amusement pier at night: loose signs banging in the wind, rusted rides creaking, waves under the boards. *(out in the weather)* | signs banging, old rides creaking, the sea | wind_gust (60s), fire_escape (120s), gulls (200s), foghorn (300s) |
| `rooftop` | Wind gusting over a city rooftop at night, traffic far below, a police siren far away now and then. *(out in the weather)* | wind, the city down below, sirens | wind_gust (35s), siren (70s), cape (90s), helicopter (180s), grapple (240s), gunshots (420s), thunder (600s), vent_hum (300s), plane (400s), horn (150s), train (300s) |
| `rooftop_day` | A city rooftop by day: wind, an air-conditioning unit humming, traffic noise rising from the street, pigeons. *(out in the weather)* | wind, traffic below, pigeons | pigeons (90s), horn (60s), siren (200s), helicopter (300s), plane (300s) |
| `stakeout` | Perched still on a high ledge at night: a light wind, the city's hum far below, almost silent. *(out in the weather)* | almost nothing — wind, the city far below | siren (150s), wind_gust (120s), dog (300s), horn (200s), train (400s) |
| `gliding` | Wind rushing past at speed while gliding high above a city at night, fabric snapping. *(cuts in: grapple, cape, landing_roof; out in the weather)* | wind roaring past | wind_gust (25s), cape (30s), grapple (90s), siren (120s), jet (400s), landing_roof (60s) |
| `running` | Fast running footsteps on pavement and heavy steady breathing. *(cuts in: breath; out in the weather)* | your own feet and breathing, the street | car_pass (50s), dog (140s), horn (100s) |
| `walking` | Footsteps walking along a city pavement, traffic passing, the odd voice going by. *(over chatter; out in the weather)* | traffic, people passing | car_pass (35s), horn (60s), dog (150s), bus (150s), bike_bell (200s), laugh_group (180s), siren (200s), heels (200s) |
| `crime_scene` | An active crime scene at night: police radios crackling, patrol cars idling, officers talking indistinctly, crime-scene tape snapping in the wind, a crowd murmuring behind the cordon. *(out in the weather)* | police radios, patrol cars idling, people behind the tape | police_radio (30s), camera_shutter (60s), siren (120s), car_door (90s), shout (180s) |
| `fire_scene` | A building on fire at night: flames roaring and crackling, fire hoses spraying, engines idling, radios. *(out in the weather)* | the fire, hoses, engines | firetruck (120s), shout (60s), glass (90s), police_radio (60s) |
| `car` | Inside a moving car: steady engine hum, tyre and road noise, an indicator ticking now and then. | the car, the road | horn (70s), motorbike (140s), siren (160s), car_pass (50s), truck_pass (120s), brakes (400s) |
| `taxi` | In the back of a city cab: the engine, road noise, the meter ticking. | the cab, the driver's radio | horn (40s), taxi_radio (90s), siren (160s), brakes (300s) |
| `bus_ride` | Riding a city bus: diesel engine droning, the bus rattling, passengers murmuring. | the bus, people talking, the stop bell | bus_bell (70s), announcement (150s), horn (90s), laugh_group (200s) |
| `subway` | Inside a subway carriage: wheels rumbling on the rails, the carriage rattling, a muffled announcement. | the train rattling, an announcement | subway_brakes (120s), announcement (160s), laugh_group (200s) |
| `platform` | A subway platform: a crowd waiting, echoing footsteps, a train rumbling in the tunnel. | the platform, a train coming | subway_brakes (90s), announcement (120s), heels (150s) |
| `station` | A big railway station concourse: echoing crowd chatter, rolling suitcases, departure chimes. | the station, announcements | announcement (60s), heels (120s), laugh_group (180s) |
| `bike` | Riding a motorcycle through a city at night: engine roar, wind buffeting a helmet, traffic whipping past. | the bike's engine, the wind | horn (80s), truck_pass (100s), siren (200s) |
| `flying` | Inside an armoured flight suit over a city at night: jet thrusters roaring, wind tearing past the helmet, soft system beeps. | the thrusters, the wind, the suit's systems | computer_beep (60s), helicopter (300s), thunder (500s) |
| `jet_cabin` | Inside a private jet cruising: a deep steady engine hum, air vents hissing. | the plane's engines | cabin_chime (300s) |
| `helicopter_cabin` | Inside a helicopter in flight: heavy rotor thump, turbine whine, radio static. | the rotors | — |
| `boat` | On a small motorboat at night: the outboard motor droning, water slapping the hull, wind. *(out in the weather)* | the motor, the water | foghorn (150s), gulls (120s), hull (60s) |
| `airport` | An airport terminal: an echoing crowd, rolling suitcases, a jet roaring outside the glass. *(rain on the windows)* | the terminal, announcements | announcement (60s), plane (120s) |
| `office` | A quiet office: keyboards typing, air conditioning humming, a phone ringing in the distance. *(rain on the windows)* | the office — keyboards, a phone somewhere | keyboard (40s), phone_ring (120s), printer (160s), elevator (200s), chair (120s), coffee_pour (300s) |
| `exec_office` | A quiet executive office high in a tower: air conditioning, the city faint through thick glass, a clock. *(rain on the windows)* | quiet — the city faint through the glass | phone_ring (300s), helicopter (500s), elevator (400s), pen (200s) |
| `boardroom` | A boardroom mid-meeting: muffled indistinct voices, papers shuffled, chairs creaking, air conditioning. *(rain on the windows)* | a meeting going on | pages (90s), chair (100s), cough (200s), pen (150s) |
| `lab` | A high-tech workshop: servers humming, cooling fans, soft electronic beeps. | servers humming, beeps | computer_beep (45s), keyboard (60s), door (300s), servo (200s), drill (300s) |
| `server_room` | A server room: fans and cooling roaring, electronic chirps. | the servers roaring | computer_beep (60s), door (300s) |
| `workshop` | A workshop: a lathe whirring, tools clinking on a bench. | machines, tools | drill (60s), hammer (70s), welding (120s) |
| `garage` | A garage: a car engine idling, tools on concrete, a compressor kicking on. | the garage, an engine | engine_rev (90s), hammer (120s), drill (150s) |
| `hangar` | A huge aircraft hangar: echoing space, tools clanking, a jet engine idling, wind at the doors. | the hangar, an engine running, tools | turbine (120s), hammer (90s), drill (120s), door (200s), jet (300s) |
| `hospital` | A hospital corridor: monitors beeping in the distance, quiet footsteps, a muffled intercom. *(rain on the windows)* | monitors beeping, the intercom | intercom (90s), pager (120s), monitor_alarm (300s), gurney (150s), door (150s) |
| `er` | A busy hospital emergency room: urgent indistinct voices, gurneys rattling, monitors beeping, phones. | the ER — monitors, people rushing | gurney (60s), monitor_alarm (90s), intercom (80s), pager (70s), phone_ring (120s) |
| `police` | A busy police station: phones ringing, radio chatter, a door buzzing. *(rain on the windows)* | phones, radio chatter, the bullpen | police_radio (45s), phone_ring (90s), door (120s), buzzer (200s), handcuffs (300s), keyboard (120s), laugh_group (240s), coffee_pour (300s) |
| `police_office` | A commissioner's office at night: the bullpen muffled through the door, a radio murmuring, a clock ticking. *(rain on the windows)* | the bullpen through the door, the radio | police_radio (90s), phone_ring (200s), knock (400s), door (200s) |
| `court` | A courthouse corridor: echoing footsteps, hushed indistinct conversations, a heavy door. | the courthouse corridor | footsteps (60s), door (120s), cough (200s) |
| `prison` | A prison corridor: buzzers, steel doors clanging, distant shouting, fluorescent hum. | doors clanging, buzzers, shouting somewhere | cell_door (50s), buzzer (70s), shout (90s), footsteps (100s) |
| `asylum` | A corridor in an old asylum: fluorescent lights buzzing, a distant wail, a door clanging, footsteps echoing. | lights buzzing, someone wailing down the hall | cell_door (90s), scream (200s), buzzer (150s), footsteps (120s) |
| `library` | A quiet library: pages turning, a cough, a chair creaking, the air conditioning. *(rain on the windows)* | quiet — pages, the odd cough | pages (40s), cough (120s), chair (150s), footsteps (200s) |
| `campus` | A university campus: students chatting, footsteps on paths, a bell in the distance. *(out in the weather)* | students about, footsteps | bell (300s), birds (90s), laugh_group (120s), bike_bell (180s), skateboard (200s) |
| `lecture` | A lecture hall: a lecturer's voice echoing indistinctly at the front, pens scratching, seats creaking. | a lecture going on | cough (90s), pages (60s), chair (120s) |
| `church` | Inside a large old church: echoing quiet, a creak of pews, footsteps on stone. | the quiet of the church | footsteps (120s), church_door (300s), cough (300s) |
| `cafe` | A busy coffee shop: cups and saucers clinking, an espresso machine hissing, murmured conversation. *(rain on the windows)* | the café — cups, the espresso machine | espresso (45s), till (90s), glasses (150s), blender (200s), door_chime (150s), laugh_group (200s) |
| `diner` | A late-night diner: a griddle sizzling, plates clattering, coffee being poured, a few quiet conversations. *(rain on the windows)* | the griddle, plates, coffee being poured | diner_bell (60s), griddle (70s), coffee_pour (90s), cutlery (150s), door_chime (150s) |
| `restaurant` | An upscale restaurant: soft murmured conversation, cutlery on china, wine glasses. *(rain on the windows)* | a restaurant — quiet talk, cutlery | champagne (300s), glasses (120s), cutlery (120s), laugh_group (180s) |
| `bar` | A lively bar at night: glasses clinking, crowd chatter and laughter. | the bar — glasses, people laughing | glasses (40s), cheer (110s), door (150s), laugh_group (90s) |
| `club` | A nightclub: heavy bass thumping, a packed crowd shouting over it. | the club, the bass, the crowd | glasses (60s), cheer (120s) |
| `gala` | A charity gala in a grand ballroom: an elegant crowd murmuring, champagne glasses clinking, a string quartet faint in the distance. | the gala crowd, glasses, a quartet somewhere | camera_shutter (120s), applause (300s), champagne (150s), laugh_group (120s) |
| `hotel_lobby` | A grand hotel lobby: soft conversations, rolling luggage, a revolving door, a reception bell. | a hotel lobby | elevator (90s), heels (120s), door (200s) |
| `cinema` | A cinema lobby: a popcorn machine popping, crowd chatter, a ticket machine beeping. | the cinema lobby | laugh_group (100s), till (120s) |
| `mall` | A shopping mall: echoing crowd chatter, an escalator humming, shop doors chiming, a fountain. | the mall | door_chime (80s), announcement (200s), laugh_group (120s) |
| `grocery` | A supermarket: shopping trolleys rattling, checkout scanners beeping in the distance, a fridge humming. | the shop — trolleys, the checkouts | trolley (40s), checkout (50s), announcement (180s), rustle (90s) |
| `stadium` | A ballpark crowd: chatter, cheers rising and falling, vendors calling out. *(out in the weather)* | the crowd at the game | crowd_roar (60s), vendor (80s), cheer (120s) |
| `gym` | A gym: weights clanking, a treadmill whirring, a heavy bag being hit. *(cuts in: barbell, effort)* | weights clanking, treadmills | weights (30s), barbell (70s), treadmill_beep (120s), effort (60s) |
| `boxing` | A boxing gym: heavy bags thudding, a speed bag rattling, skipping ropes slapping. *(cuts in: heavy_bag, effort, breath)* | bags thudding, a speed bag, ropes | heavy_bag (20s), speed_bag (50s), skip_rope (60s), whistle (150s) |
| `dojo` | A quiet dojo: bare feet shuffling on mats, breathing, the hum of the lights. *(bursts and rests; cuts in: staff, effort)* | the mats, strikes, breathing | kiai (40s), staff (50s), footwork (30s) |
| `acrobatics` | A big empty training hall: a high ceiling's echo, ropes and rigging creaking softly, a ventilation hum. *(bursts and rests; cuts in: landing_mat, effort, breath, trapeze)* | the hall echoing, the rigging creaking | landing_mat (12s), trapeze (25s), tumble (30s), net (60s), effort (20s), breath (40s) |
| `studio` | An empty dance studio: soft footsteps and turns on a sprung wooden floor, a creak of the barre. *(bursts and rests; cuts in: footwork, breath)* | the studio floor | footwork (30s) |
| `pool` | An indoor pool: water splashing and echoing, the filters humming. *(cuts in: breath)* | the pool, splashing | splash (60s), whistle (200s) |
| `cave_training` | The training floor of a vast cave: echoing mats, water dripping, a hum of machines, bats far off. *(bursts and rests; cuts in: landing_mat, effort, heavy_bag)* | the cave echoing, the mats | landing_mat (15s), heavy_bag (25s), staff (40s), drip (40s), bats (120s) |
| `cave` | A vast cave: water dripping and echoing, bats fluttering far off, the low hum of computers. | the cave — water dripping, the computers | drip (25s), bats (90s), batcomputer (60s), welding (300s), engine_rev (400s) |
| `manor` | A large quiet old house: a grandfather clock ticking, old wood creaking, wind outside. *(rain on the windows)* | the house — the clock, old wood creaking | clock (600s), door (240s), kettle (500s), footsteps (300s) |
| `home` | A quiet apartment: a refrigerator humming, city traffic faint through a window. *(rain on the windows)* | home — quiet, traffic outside | phone_buzz (200s), kettle (400s), siren (400s), neighbours (500s), pipes (400s), horn (300s) |
| `home_tv` | A living room with the television on low: muffled TV voices, a sofa creaking, the city outside. *(rain on the windows)* | the TV on low | tv_laugh (60s), microwave (300s), phone_buzz (200s) |
| `game_night` | A living room on a games night: a video game playing loud on the TV, controllers clicking, friends trash-talking and laughing, indistinct. *(rain on the windows)* | the game on the TV, everyone trash-talking | controller (25s), game_boom (30s), game_groan (70s), game_cheer (90s), phone_buzz (300s), fridge (240s) |
| `movie_night` | A living room on a movie night: a film playing on the TV, muffled dialogue and score, a sofa creaking, people murmuring now and then. *(rain on the windows)* | the film playing, people murmuring | film_boom (60s), popcorn (50s), film_laugh (120s), film_gasp (240s), microwave (400s) |
| `bedroom` | A bedroom at night: very quiet, a clock ticking, a car passing outside now and then. *(rain on the windows)* | quiet — a car outside now and then | car_pass (120s), siren (400s) |
| `kitchen` | Cooking in a home kitchen: a pan sizzling, a pot simmering, a knife on a chopping board. *(rain on the windows)* | the kitchen — the pan, the chopping board | sizzle (35s), chop (50s), tap (90s), plates (120s), microwave (300s), fridge (200s) |
| `shower` | A shower running in a tiled bathroom, water drumming steadily. | the shower running | shower_door (240s) |
| `cats_home` | A quiet, stylish apartment at night with cats: soft purring close by, paws padding across the floor. *(rain on the windows)* | the cats, purring | purr (60s), meow (90s), cat_jump (150s) |
| `dog_home` | A small apartment with a dog: a dog's tags jingling, paws clicking on the floor, a contented huff. *(rain on the windows)* | the dog padding about | dog_tags (70s), dog_bark (160s) |
| `houseboat` | Inside a houseboat at a marina at night: water lapping at the hull, ropes creaking, a halyard tapping a mast. *(rain on the windows)* | water against the hull, ropes creaking | hull (50s), gulls (120s), foghorn (300s) |
| `clocktower` | Inside a clock tower: huge gears ticking and turning slowly overhead, computer fans humming. *(rain on the windows)* | the clockwork overhead, the computers | gears (40s), computer_beep (70s), keyboard (80s) |
| `safehouse` | A bare safehouse in a run-down building: a dripping pipe, distant traffic, the building creaking. *(rain on the windows)* | a pipe dripping, the building creaking | drip (30s), slide (300s), siren (150s), door (200s), neighbours (400s) |
| `warehouse` | An abandoned warehouse: pigeons in the rafters, water dripping, loose metal creaking in the wind. | pigeons in the rafters, dripping, metal creaking | pigeons (120s), drip (60s), fire_escape (200s), rat (150s), door (200s) |
| `sewer` | A sewer tunnel: water rushing, dripping, a deep echo. | water rushing, the echo | rat (80s), drip (30s) |
| `scuffle` | A fight nearby, muffled: grunts, blows landing, a body hitting crates, glass breaking. *(cuts in: punch, kick, effort)* | the fight | punch (7s), kick (14s), glass (45s), gunshots (300s), scream (400s), body_fall (25s), pipe (60s), batarang (90s), smoke (120s), taser (200s), thugs (60s) |
| `gunfight` | A firefight in a warehouse: gunfire echoing, bullets pinging off metal, shouting. *(cuts in: ricochet, gunshots)* | gunfire | gunfire_burst (15s), ricochet (25s), gunshots (20s), glass (60s), thugs (40s), body_fall (60s), smoke (120s) |

## Layers and weather

| Loop | What it is |
|---|---|
| `chatter` | A busy pavement crowd: many overlapping indistinct conversations and footsteps of passers-by. |
| `rain_layer` | Steady rain falling on a city street, water running in gutters, tyres hissing on wet roads. |
| `rain_roof` | Heavy rain drumming on a rooftop at night, wind gusting, water pouring off ledges. |
| `storm` | A thunderstorm over a city at night: heavy rain, wind, thunder rolling and cracking. |
| `snow` | A snowy city street at night, muffled and hushed, wind sighing, footsteps crunching. |
| `wind_layer` | Strong wind gusting through city streets, whistling around buildings and street signs. |
| `window_rain` | Rain pattering steadily against a window, heard from inside a quiet room. |
| `car_rain` | Rain drumming on a car roof, windscreen wipers thumping steadily. |
| `crickets` | Crickets and insects chirping in grass on a warm night. |

## Sounds

| Sound | Heard as | From |
|---|---|---|
| `horn` | a car horn | close by, beside them, far off, going past |
| `horn_truck` | a truck's air horn | beside them, far off |
| `siren` | a police siren | far off, going past, beside them |
| `ambulance` | an ambulance | going past, far off |
| `firetruck` | a fire engine | going past, far off |
| `car_pass` | a car going past | going past, close by |
| `truck_pass` | a lorry going past | going past |
| `motorbike` | a motorbike | going past, far off |
| `bus` | a bus | beside them, going past |
| `brakes` | tyres screeching | beside them, far off |
| `crash` | a car crash | far off |
| `alarm` | a car alarm | far off, beside them |
| `dog` | a dog barking | far off, beside them |
| `scream` | someone screaming | far off |
| `gunshots` | gunshots | far off, close by, beside them |
| `helicopter` | a helicopter | going past, far off |
| `train` | a train | going past, far off |
| `thunder` | thunder | far off, beside them |
| `shout` | someone shouting | far off, beside them |
| `laugh_group` | people laughing as they went by | going past, close by |
| `whistle` | a traffic cop's whistle | close by |
| `jackhammer` | a jackhammer | close by, far off |
| `reverse_beep` | a truck backing up | close by |
| `pigeons` | pigeons taking off | beside them, close by |
| `bike_bell` | a bike bell | going past |
| `skateboard` | a skateboard | going past |
| `heels` | someone in heels | going past |
| `manhole` | a manhole cover | going past |
| `steam` | steam from a vent | beside them |
| `dumpster` | a dumpster lid | close by |
| `bottle_kick` | a bottle skittering | beside them |
| `fire_escape` | a fire escape rattling | close by |
| `cat_yowl` | alley cats | far off |
| `plane` | a plane overhead | far off |
| `ferry` | a ferry horn | far off |
| `foghorn` | a foghorn | far off |
| `gulls` | gulls | close by, beside them |
| `vendor` | a vendor calling out | close by |
| `church_bells` | church bells | far off |
| `car_door` | a car door | close by |
| `owl` | an owl | far off |
| `crickets_burst` | crickets | beside them |
| `wind_gust` | the wind | beside them |
| `grapple` | the grapnel | beside them |
| `cape` | the cape in the wind | beside them |
| `glide_whoosh` | air rushing past | beside them |
| `landing_roof` | boots on the roof | beside them |
| `vent_hum` | an air-con unit | close by |
| `punch` | a punch landing | beside them |
| `kick` | a kick | beside them |
| `glass` | glass breaking | beside them, close by |
| `body_fall` | someone going down | beside them |
| `batarang` | a batarang | beside them |
| `smoke` | a smoke pellet | beside them |
| `taser` | an electric shock | beside them |
| `pipe` | a pipe on a railing | beside them |
| `gunfire_burst` | automatic fire | close by |
| `ricochet` | a ricochet | beside them |
| `slide` | a gun being racked | beside them |
| `thugs` | men shouting | close by |
| `landing_mat` | a landing on the mat | beside them |
| `trapeze` | the trapeze | beside them |
| `net` | the safety net | beside them |
| `tumble` | tumbling | beside them |
| `heavy_bag` | the heavy bag | beside them |
| `speed_bag` | a speed bag | close by |
| `skip_rope` | a skipping rope | close by |
| `barbell` | a barbell dropped | beside them, close by |
| `weights` | weights | beside them |
| `effort` | a grunt of effort | beside them |
| `breath` | heavy breathing | beside them |
| `kiai` | a shout and a strike | close by |
| `staff` | a staff | beside them |
| `footwork` | quick footwork | beside them |
| `treadmill_beep` | a treadmill beeping | close by |
| `splash` | a dive | beside them |
| `door` | a door | close by, beside them |
| `knock` | a knock at the door | beside them |
| `doorbell` | the door buzzer | close by |
| `phone_buzz` | a phone buzzing | beside them |
| `phone_ring` | a phone ringing | close by |
| `keyboard` | typing | beside them |
| `printer` | the printer | close by |
| `elevator` | the lift | close by |
| `kettle` | the kettle | beside them |
| `clock` | the clock chiming | close by |
| `computer_beep` | the computer | beside them |
| `police_radio` | police radio | close by |
| `announcement` | an announcement | far off |
| `microwave` | the microwave | beside them |
| `fridge` | the fridge | beside them |
| `tv_laugh` | the TV | close by |
| `vacuum` | a vacuum cleaner | far off |
| `washing` | the washing machine | close by |
| `pipes` | the pipes | close by |
| `neighbours` | the neighbours | far off |
| `baby` | a baby crying | far off |
| `pages` | pages turning | beside them |
| `pen` | a pen clicking | beside them |
| `chair` | a chair creaking | beside them |
| `cough` | someone coughing | close by |
| `coffee_pour` | coffee being poured | beside them |
| `pager` | a pager | close by |
| `monitor_alarm` | a monitor alarm | close by |
| `gurney` | a gurney | going past |
| `intercom` | the intercom | far off |
| `cell_door` | a cell door | close by |
| `buzzer` | a security buzzer | close by |
| `handcuffs` | handcuffs | beside them |
| `camera_shutter` | cameras | beside them |
| `applause` | applause | close by |
| `champagne` | a cork popping | beside them |
| `cutlery` | cutlery | beside them |
| `diner_bell` | the order bell | close by |
| `griddle` | the griddle | close by |
| `blender` | a blender | close by |
| `door_chime` | the shop bell | close by |
| `trolley` | a shopping trolley | beside them |
| `checkout` | the checkout | close by |
| `espresso` | the espresso machine | close by |
| `till` | the till | close by |
| `glasses` | glasses clinking | close by |
| `cheer` | a cheer | close by |
| `crowd_roar` | the crowd | far off |
| `bell` | a bell | far off |
| `birds` | birds | beside them |
| `hose` | the hose | beside them |
| `watering_can` | the watering can | beside them |
| `shears` | the shears | beside them |
| `trowel` | digging | beside them |
| `wheelbarrow` | a wheelbarrow | close by |
| `welding` | a welding torch | beside them |
| `drill` | a drill | beside them |
| `hammer` | hammering | beside them, close by |
| `engine_rev` | an engine revving | close by |
| `turbine` | a jet engine spooling | close by |
| `servo` | servos | beside them |
| `batcomputer` | the computer | beside them |
| `bats` | bats | close by |
| `drip` | water dripping | beside them |
| `subway_brakes` | a train coming in | beside them |
| `bus_bell` | the stop bell | beside them |
| `taxi_radio` | the cab's radio | close by |
| `cabin_chime` | a cabin chime | close by |
| `rat` | rats | beside them |
| `church_door` | a heavy door | far off |
| `meow` | the cat | beside them |
| `purr` | the cat purring | beside them |
| `cat_jump` | the cat | beside them |
| `dog_bark` | the dog | beside them |
| `dog_tags` | the dog | beside them |
| `sizzle` | the pan | beside them |
| `chop` | chopping | beside them |
| `plates` | plates | beside them |
| `tap` | the tap | beside them |
| `shower_door` | the shower door | beside them |
| `hull` | the boat | beside them |
| `gears` | the clockwork | beside them |
| `footsteps` | footsteps | close by |
| `rustle` | a bag | beside them |
| `jet` | a jet | going past |
| `window_rain` | rain on the window | beside them |
| `controller` | someone mashing a controller | beside them |
| `game_boom` | the game | close by, beside them |
| `game_groan` | them groaning at the game | close by |
| `game_cheer` | them cheering | close by |
| `popcorn` | popcorn | beside them |
| `film_boom` | the film | close by, far off |
| `film_laugh` | everyone laughing at the film | close by |
| `film_gasp` | a gasp at the film | close by |
